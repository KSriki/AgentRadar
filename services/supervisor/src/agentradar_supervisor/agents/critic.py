"""Critic agent — refactored from scripts/critic.py to fit the Agent protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastmcp import Client

from agentradar_core import get_logger
from agentradar_store import get_s3_client, get_slm_client

log = get_logger(__name__)


_CYPHER_IDENT = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

KNOWN_PREDICATES: set[str] = {
    "INSTANCE_OF", "INTRODUCED_BY", "FIRST_SEEN_IN", "IMPLEMENTS",
    "COMPETES_WITH", "SUPERSEDES", "MENTIONED_IN", "GOVERNED_BY", "DEPRECATES",
}


FAITHFULNESS_PROMPT = """You are a strict faithfulness validator for a knowledge
graph about agentic AI standards and frameworks.

You will be given a source document and a proposed claim of the form
(SUBJECT, PREDICATE, OBJECT). Your job is to decide whether the source
document genuinely supports that claim.

DECIDE "approved" ONLY IF:
- The source document explicitly mentions the SUBJECT, AND
- The source document supports the relationship between SUBJECT and OBJECT
  in the way the PREDICATE describes

DECIDE "rejected" IF:
- The SUBJECT is not mentioned in the source, OR
- The OBJECT is not mentioned in connection with the subject, OR
- The PREDICATE describes a relationship the source does not support

PREDICATE meanings:
- INSTANCE_OF: subject is an example/instance of the type named by object
- INTRODUCED_BY: subject was created/published/announced by object
- FIRST_SEEN_IN: subject was first publicly observed in source object
- IMPLEMENTS: subject is an implementation of the spec/protocol object
- COMPETES_WITH: subject is presented as an alternative to object
- SUPERSEDES: subject explicitly replaces or deprecates object
- MENTIONED_IN: subject is named in the source object (the weakest claim)
- GOVERNED_BY: subject is under governance/maintenance of object
- DEPRECATES: subject is marked deprecated by/in favor of object

Be conservative. When in doubt, reject. False approvals corrupt the graph
permanently; false rejections just mean a Scout proposes again later.

Respond ONLY with valid JSON in this exact shape, no prose, no markdown fences:
{"verdict": "approved" | "rejected", "reasoning": "one sentence", "confidence": 0.0 to 1.0}
"""


@dataclass
class TripleToReview:
    triple_id: str
    subject: str
    predicate: str
    object: str
    source_id: str
    confidence: float
    proposer_agent: str


class Critic:
    """Validates pending triples through structural → ontology → faithfulness pipeline."""

    name = "critic"

    def __init__(self, batch_limit: int = 50, dry_run: bool = False) -> None:
        self.batch_limit = batch_limit
        self.dry_run = dry_run

    async def run(self, mcp: Client) -> dict[str, Any]:
        log.info("critic.run.start", limit=self.batch_limit, dry_run=self.dry_run)

        result = await mcp.call_tool("list_pending_triples", {"limit": self.batch_limit})
        pending = result.data
        if not pending:
            log.info("critic.run.no_pending")
            return {"reviewed": 0, "approved": 0, "rejected": 0}

        log.info("critic.run.found_pending", count=len(pending))

        results = []
        for raw in pending:
            triple = TripleToReview(
                triple_id=raw["id"],
                subject=raw["subject"],
                predicate=raw["predicate"],
                object=raw["object"],
                source_id=raw["source_id"],
                confidence=raw["confidence"],
                proposer_agent=raw["proposer_agent"],
            )
            try:
                outcome = await self._review_one(mcp, triple)
                results.append(outcome)
            except Exception as exc:
                log.exception(
                    "critic.review_failed",
                    triple_id=triple.triple_id, error=str(exc),
                )

        summary = {
            "reviewed": len(results),
            "approved": sum(1 for r in results if r["decision"] == "approved"),
            "rejected": sum(1 for r in results if r["decision"] == "rejected"),
            "by_stage": {
                stage: sum(1 for r in results if r["stage"] == stage)
                for stage in ("structural", "ontology", "faithfulness")
            },
        }
        log.info("critic.run.done", **summary)
        return summary

    # ----- pipeline stages -------------------------------------------------

    async def _review_one(self, mcp: Client, triple: TripleToReview) -> dict[str, Any]:
        ok, reason = self._structural_check(triple)
        if not ok:
            return await self._decide(mcp, triple, False, reason or "?", "structural")

        ok, reason = self._ontology_check(triple)
        if not ok:
            return await self._decide(mcp, triple, False, reason or "?", "ontology")

        approved, reasoning, _confidence = await self._faithfulness_check(triple)
        return await self._decide(mcp, triple, approved, reasoning, "faithfulness")

    def _structural_check(self, triple: TripleToReview) -> tuple[bool, str | None]:
        if not triple.subject.strip():
            return False, "empty subject"
        if not triple.object.strip():
            return False, "empty object"
        if not triple.source_id.strip():
            return False, "missing source_id"
        if not _CYPHER_IDENT.match(triple.predicate):
            return False, f"predicate {triple.predicate!r} fails identifier regex"
        return True, None

    def _ontology_check(self, triple: TripleToReview) -> tuple[bool, str | None]:
        if triple.predicate not in KNOWN_PREDICATES:
            return False, f"predicate {triple.predicate!r} not in ontology"
        return True, None

    async def _faithfulness_check(
        self, triple: TripleToReview
    ) -> tuple[bool, str, float]:
        source_text = await self._fetch_source_text(triple.source_id)
        if source_text is None:
            return False, f"could not fetch source artifact for {triple.source_id}", 1.0

        slm = get_slm_client()
        raw = await slm.generate(
            system=FAITHFULNESS_PROMPT,
            user=(
                f"SOURCE DOCUMENT:\n{source_text}\n\n"
                f"PROPOSED CLAIM:\n"
                f"  SUBJECT:   {triple.subject}\n"
                f"  PREDICATE: {triple.predicate}\n"
                f"  OBJECT:    {triple.object}\n"
            ),
            max_tokens=200,
            temperature=0.0,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()

        try:
            parsed = json.loads(raw)
            verdict = parsed.get("verdict", "rejected")
            reasoning = parsed.get("reasoning", "no reasoning provided")
            confidence = float(parsed.get("confidence", 0.5))
        except (json.JSONDecodeError, ValueError):
            return False, "SLM returned unparseable response", 0.0

        if verdict not in ("approved", "rejected"):
            return False, f"SLM returned invalid verdict: {verdict!r}", 0.0

        return verdict == "approved", reasoning, confidence

    async def _fetch_source_text(self, source_id: str) -> str | None:
        """
        Fetch the raw artifact for a source and render it as plain text for
        the SLM to read. Each source type has its own S3 key convention and
        its own JSON shape, so we dispatch by the source_id's prefix.

        Returns None if the source artifact can't be fetched or its prefix
        is unrecognized — the Critic correctly rejects on None.
        """
        # Map source-ID prefix → S3 key + text rendering function.
        # Adding a new source type is one new entry in this dict.
        if ":" not in source_id:
            log.warning("critic.malformed_source_id", source_id=source_id)
            return None
        prefix, identifier = source_id.split(":", 1)

        s3 = get_s3_client()
        try:
            if prefix == "arxiv":
                body = await s3.get_artifact(f"arxiv/{identifier}.json")
                payload = json.loads(body.decode("utf-8"))
                return (
                    f"TITLE: {payload.get('title', '')}\n\n"
                    f"ABSTRACT: {payload.get('summary', '')}"
                )

            if prefix == "tavily":
                body = await s3.get_artifact(f"tavily/{identifier}.json")
                payload = json.loads(body.decode("utf-8"))
                return (
                    f"TITLE: {payload.get('title', '')}\n\n"
                    f"URL: {payload.get('url', '')}\n\n"
                    f"CONTENT: {payload.get('content', '')}"
                )

            log.warning("critic.unknown_source_type",
                        source_id=source_id, prefix=prefix)
            return None

        except Exception as exc:
            log.warning(
                "critic.fetch_artifact_failed",
                source_id=source_id, error=str(exc),
            )
            return None

    async def _decide(
        self, mcp: Client, triple: TripleToReview,
        approved: bool, reasoning: str, stage: str,
    ) -> dict[str, Any]:
        decision = "approved" if approved else "rejected"
        log.info(
            "critic.decided",
            triple_id=triple.triple_id, decision=decision,
            stage=stage, reasoning=reasoning,
        )
        if self.dry_run:
            return {
                "triple_id": triple.triple_id,
                "decision": decision, "stage": stage, "reasoning": reasoning,
            }
        if approved:
            await mcp.call_tool("approve_triple", {"triple_id": triple.triple_id})
        else:
            await mcp.call_tool(
                "reject_triple",
                {"triple_id": triple.triple_id, "reason": f"[{stage}] {reasoning}"},
            )
        return {
            "triple_id": triple.triple_id,
            "decision": decision, "stage": stage, "reasoning": reasoning,
        }