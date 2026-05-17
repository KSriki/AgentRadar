"""
TrendScout — pulls trending signals from heterogeneous sources and
funnels them through the same SLM-extraction + proposer-critic
pipeline as the other Scouts.

Each TrendScout invocation polls ONE source (round-robin across
GitHub trending, HN, lab RSS). Same single-source-per-run discipline
as the other Scouts; the supervisor handles diversity via scheduling.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from agentradar_core import get_logger
from agentradar_store import get_slm_client
from fastmcp import Client

from agentradar_supervisor.agents.trend_sources import (
    TrendItem,
    TrendSource,
)

log = get_logger(__name__)


EXTRACTION_PROMPT = """You are an extractor for an agentic-AI knowledge tracker.

Given a trending signal — a GitHub repo, Hacker News story, or lab blog
announcement — extract concept names that should be tracked as nodes in a
knowledge graph about agentic AI: protocol names, framework names,
architectural pattern names, model names, evaluation methods, or notable
specific tools.

RULES:
- Extract ONLY proper-noun concepts (e.g., "MCP", "LangGraph", "ReAct").
- Do NOT extract generic terms ("agent", "model", "framework").
- Do NOT invent concept names not present in the text.
- Return at most 8 concepts.
- Use the exact casing as it appears in the text.
- If no concepts are present, return an empty list.

Respond ONLY with valid JSON in this shape, no prose, no markdown fences:
{"concepts": ["ConceptA", "ConceptB"]}
"""


class TrendScout:
    """Single-source-per-run TrendScout. Source is injected at construction."""

    name = "scout-trends"

    def __init__(self, source: TrendSource) -> None:
        self._source = source

    async def run(self, mcp: Client) -> dict[str, Any]:
        log.info("scout_trends.run.start", source=self._source.name)

        # Step 1: fetch (source-specific)
        try:
            items = await self._source.fetch()
        except Exception as exc:
            log.exception("scout_trends.fetch_failed", source=self._source.name)
            return {"results_fetched": 0, "error": str(exc)}

        if not items:
            log.info("scout_trends.run.no_items", source=self._source.name)
            return {"source": self._source.name, "results_fetched": 0}

        # Step 2: dedupe in-memory by URL
        seen: set[str] = set()
        unique = [i for i in items if not (i.url in seen or seen.add(i.url))]

        # Step 3: store raw artifacts
        await self._store_raw(mcp, unique)

        # Steps 4 + 5: extract concepts, propose
        total_mentions = 0
        total_proposals = 0
        items_with_concepts = 0
        for item in unique:
            concepts = await self._extract_concepts(item)
            if not concepts:
                log.info(
                    "scout_trends.item.no_concepts",
                    source_id=item.source_id,
                    title=item.title[:80],
                )
                continue
            stats = await self._propose_findings(mcp, item, concepts)
            total_mentions += stats["mentions"]
            total_proposals += stats["proposals"]
            items_with_concepts += 1
            log.info(
                "scout_trends.item.done",
                source_id=item.source_id,
                title=item.title[:80],
                concepts=concepts,
                **stats,
            )

        summary = {
            "source": self._source.name,
            "results_fetched": len(unique),
            "results_with_concepts": items_with_concepts,
            "mentions_recorded": total_mentions,
            "triples_proposed": total_proposals,
        }
        log.info("scout_trends.run.done", **summary)
        return summary

    # ----- step 3 ----------------------------------------------------------

    async def _store_raw(self, mcp: Client, items: list[TrendItem]) -> None:
        for item in items:
            payload = json.dumps(
                {
                    "source_kind": item.source_kind,
                    "source_id": item.source_id,
                    "url": item.url,
                    "title": item.title,
                    "summary": item.summary,
                    "published_at": item.published_at.isoformat(),
                    "extra": item.extra,
                    "fetched_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
                default=str,
            )
            await mcp.call_tool(
                "put_text_artifact",
                {
                    "key": item.s3_key,
                    "content": payload,
                    "content_type": "application/json",
                },
            )
        log.info("scout_trends.store_raw.done", count=len(items))

    # ----- step 4 ----------------------------------------------------------

    async def _extract_concepts(self, item: TrendItem) -> list[str]:
        slm = get_slm_client()
        text = await slm.generate(
            system=EXTRACTION_PROMPT,
            user=f"TITLE: {item.title}\n\nSUMMARY: {item.summary}",
            max_tokens=256,
        )
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()
        try:
            parsed = json.loads(text)
            return [c for c in parsed.get("concepts", []) if isinstance(c, str) and c.strip()]
        except json.JSONDecodeError:
            log.warning(
                "scout_trends.extract.bad_json",
                source_id=item.source_id,
                raw=text[:200],
            )
            return []

    # ----- step 5 ----------------------------------------------------------

    async def _propose_findings(
        self, mcp: Client, item: TrendItem, concepts: list[str]
    ) -> dict[str, int]:
        observed_at = item.published_at.isoformat()
        mentions = 0
        proposals = 0
        for concept in concepts:
            await mcp.call_tool(
                "record_mention",
                {
                    "concept_name": concept,
                    "source_id": item.source_id,
                    # Map source_kind to a SourceType the schema accepts.
                    # 'blog' is closest for all three currently.
                    "source_type": "blog",
                    "observed_at": observed_at,
                },
            )
            mentions += 1
            await mcp.call_tool(
                "propose_triple",
                {
                    "proposer_agent": f"{self.name}-{item.source_kind}",
                    "subject": concept,
                    "predicate": "MENTIONED_IN",
                    "object": item.source_id,
                    "source_id": item.source_id,
                    # Slightly higher than arXiv MENTIONED_IN — trend signals
                    # are a stronger "this matters now" signal than just
                    # appearing in some abstract somewhere
                    "confidence": 0.65,
                },
            )
            proposals += 1
        return {"mentions": mentions, "proposals": proposals}
