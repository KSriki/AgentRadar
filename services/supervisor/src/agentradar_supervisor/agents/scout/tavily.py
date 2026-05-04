"""
Tavily Scout — open-web research scout for AgentRadar.

Where the arXiv Scout pulls discrete papers, the Tavily Scout asks
research-style questions ("what new agent protocols launched recently?")
and gets back AI-curated snippets from across the open web — blog posts,
press releases, conference recaps, lab announcements.

This is the Scout that hits the project's headline thesis: catching
MCP-equivalent things before they have a Wikipedia page. arXiv is
academic and stable; Tavily reaches into vendor announcements and
hot-off-the-press community discussion.

Pipeline (mirrors arxiv.py's shape with Tavily-specific tweaks):
    1. fetch     — for each configured query, ask Tavily
    2. dedupe    — by URL within run; cross-run via DB UNIQUE constraints
    3. store     — write each result's snippet to S3 as the "raw artifact"
    4. extract   — SLM concept extraction from snippet (the only LLM call)
    5. propose   — record_mention + propose_triple per (concept, source)

A note on source identity: each Tavily result becomes a Source with
id=tavily:<sha256-of-url>. The hash gives us idempotency without storing
URLs as primary keys (which can be huge), and lets the Critic later
fetch the cleaned content back from S3 for faithfulness validation —
same pattern as arxiv:<id>.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastmcp import Client

from agentradar_core import get_logger
from agentradar_store import (
    TavilyResult,
    get_slm_client,
    get_tavily_client,
)

log = get_logger(__name__)


# Default queries — chosen for high agentic-AI signal density. Override
# via env or constructor.
DEFAULT_QUERIES: list[str] = [
    "new AI agent protocols announced",
    "agent-to-agent communication standards",
    "agentic AI architectural patterns",
    "MCP Model Context Protocol updates",
    "LangGraph multi-agent orchestration",
    "AI agent framework benchmarks",
]


EXTRACTION_PROMPT = """You are an extractor for an agentic-AI knowledge tracker.

Given a web snippet (typically from a blog post, news article, or technical
documentation), extract concept names that should be tracked as nodes in a
knowledge graph about agentic AI: protocol names, framework names,
architectural pattern names, model names, evaluation method names, or
notable specific tools.

RULES:
- Extract ONLY proper-noun concepts (e.g., "MCP", "LangGraph", "ReAct", "ROMA").
- Do NOT extract generic terms (e.g., "agent", "model", "framework").
- Do NOT invent concept names not present in the text.
- Return at most 8 concepts per snippet.
- Use the exact casing as it appears in the text.
- If no concepts are present, return an empty list.

Respond ONLY with valid JSON in this shape, no prose, no markdown fences:
{"concepts": ["ConceptA", "ConceptB"]}
"""


@dataclass(frozen=True)
class TavilyArtifact:
    """One Tavily result enriched with our own identifiers and metadata."""

    result: TavilyResult
    query: str          # which query produced this result

    @property
    def source_id(self) -> str:
        """Stable Source.id derived from the URL — idempotent across runs."""
        url_hash = hashlib.sha256(self.result.url.encode()).hexdigest()[:32]
        return f"tavily:{url_hash}"

    @property
    def s3_key(self) -> str:
        return f"tavily/{self.source_id.removeprefix('tavily:')}.json"


class TavilyScout:
    """
    Open-web research Scout. Cycles through configured queries; each run
    handles ONE query (chosen by the supervisor's round-robin factory).

    Single-query-per-run is deliberate: keeps each invocation cheap and
    fast, and aligns with how the arXiv Scout handles single categories.
    The supervisor spreads work across queries via its scheduling layer,
    not via this agent's internals.
    """

    name = "scout-tavily"

    def __init__(self, query: str, max_results: int = 8) -> None:
        self.query = query
        self.max_results = max_results

    async def run(self, mcp: Client) -> dict[str, Any]:
        log.info("scout_tavily.run.start", query=self.query, max=self.max_results)

        # Step 1: fetch
        tavily = get_tavily_client()
        try:
            results = await tavily.search(self.query, max_results=self.max_results)
        except Exception as exc:
            log.exception("scout_tavily.fetch_failed", error=str(exc))
            return {"results_fetched": 0, "error": str(exc)}

        if not results:
            log.info("scout_tavily.run.no_results", query=self.query)
            return {"results_fetched": 0}

        # Wrap with our own metadata
        artifacts = [TavilyArtifact(result=r, query=self.query) for r in results]

        # Step 2: in-memory dedup by URL (cross-run dedup via DB UNIQUE)
        seen: set[str] = set()
        unique = [
            a for a in artifacts
            if not (a.result.url in seen or seen.add(a.result.url))
        ]

        # Step 3: store raw artifacts (the AI-cleaned snippet, NOT the page)
        await self._store_raw(mcp, unique)

        # Steps 4 + 5: per-result extraction and proposal
        total_mentions = 0
        total_proposals = 0
        results_with_concepts = 0
        for art in unique:
            concepts = await self._extract_concepts(art)
            if not concepts:
                log.info(
                    "scout_tavily.result.no_concepts",
                    source_id=art.source_id, url=art.result.url[:80],
                )
                continue
            stats = await self._propose_findings(mcp, art, concepts)
            total_mentions += stats["mentions"]
            total_proposals += stats["proposals"]
            results_with_concepts += 1
            log.info(
                "scout_tavily.result.done",
                source_id=art.source_id,
                title=art.result.title[:80],
                concepts=concepts,
                **stats,
            )

        summary = {
            "query": self.query,
            "results_fetched": len(unique),
            "results_with_concepts": results_with_concepts,
            "mentions_recorded": total_mentions,
            "triples_proposed": total_proposals,
        }
        log.info("scout_tavily.run.done", **summary)
        return summary

    # ----- step 3 ----------------------------------------------------------

    async def _store_raw(self, mcp: Client, arts: list[TavilyArtifact]) -> None:
        for art in arts:
            payload = json.dumps({
                "source_type": "tavily",
                "source_id": art.source_id,
                "query": art.query,
                "url": art.result.url,
                "title": art.result.title,
                "content": art.result.content,
                "score": art.result.score,
                "published_date": art.result.published_date,
                "fetched_at": datetime.now(UTC).isoformat(),
            }, indent=2)
            await mcp.call_tool("put_text_artifact", {
                "key": art.s3_key,
                "content": payload,
                "content_type": "application/json",
            })
        log.info("scout_tavily.store_raw.done", count=len(arts))

    # ----- step 4 ----------------------------------------------------------

    async def _extract_concepts(self, art: TavilyArtifact) -> list[str]:
        slm = get_slm_client()
        # Use TITLE + CONTENT — title carries strong signal, content is
        # already AI-cleaned by Tavily so it's reasonably short
        text = await slm.generate(
            system=EXTRACTION_PROMPT,
            user=f"TITLE: {art.result.title}\n\nCONTENT: {art.result.content}",
            max_tokens=256,
        )
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()
        try:
            parsed = json.loads(text)
            return [
                c for c in parsed.get("concepts", [])
                if isinstance(c, str) and c.strip()
            ]
        except json.JSONDecodeError:
            log.warning(
                "scout_tavily.extract.bad_json",
                source_id=art.source_id,
                raw=text[:200],
            )
            return []

    # ----- step 5 ----------------------------------------------------------

    async def _propose_findings(
        self, mcp: Client, art: TavilyArtifact, concepts: list[str]
    ) -> dict[str, int]:
        # Use Tavily's published_date if available; otherwise now() since
        # we're observing the result at this moment regardless
        observed_at = art.result.published_date or datetime.now(UTC).isoformat()

        mentions = 0
        proposals = 0
        for concept in concepts:
            await mcp.call_tool("record_mention", {
                "concept_name": concept,
                "source_id": art.source_id,
                "source_type": "blog",     # 'blog' is the closest existing type;
                                           # 'web' could be added to SourceType later
                "observed_at": observed_at,
            })
            mentions += 1
            await mcp.call_tool("propose_triple", {
                "proposer_agent": self.name,
                "subject": concept,
                "predicate": "MENTIONED_IN",
                "object": art.source_id,
                "source_id": art.source_id,
                # Slightly higher confidence than arxiv MENTIONED_IN because
                # Tavily's relevance score already filtered for relevance.
                # The Critic still validates faithfulness.
                "confidence": min(0.7, 0.4 + 0.3 * art.result.score),
            })
            proposals += 1
        return {"mentions": mentions, "proposals": proposals}