"""arXiv Scout — refactored from scripts/scout_arxiv.py to fit the Agent protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from fastmcp import Client

from agentradar_core import get_logger
from agentradar_store import get_slm_client

log = get_logger(__name__)


ARXIV_RSS_TEMPLATE = "https://export.arxiv.org/rss/{category}"

EXTRACTION_PROMPT = """You are an extractor for an agentic-AI knowledge tracker.

Given the title and abstract of a paper, extract concept names that are likely
to be tracked as nodes in a knowledge graph about agentic AI: protocol names,
framework names, architectural pattern names, model names, evaluation method
names, or notable specific tools.

RULES:
- Extract ONLY proper-noun concepts (e.g., "MCP", "LangGraph", "ReAct", "ROMA").
- Do NOT extract generic terms (e.g., "agent", "model", "evaluation").
- Do NOT invent concept names not present in the text.
- Return at most 8 concepts per paper.
- Use the exact casing as it appears in the text.
- If no concepts are present, return an empty list.

Respond ONLY with valid JSON in this shape, no prose, no markdown fences:
{"concepts": ["ConceptA", "ConceptB"]}
"""


@dataclass(frozen=True)
class ArxivPaper:
    arxiv_id: str
    title: str
    summary: str
    published: datetime
    authors: list[str]
    link: str

    @property
    def source_id(self) -> str:
        return f"arxiv:{self.arxiv_id}"

    @property
    def s3_key(self) -> str:
        return f"arxiv/{self.arxiv_id}.json"


class ArxivScout:
    """Pulls one arXiv RSS category, dedupes, stores raw, extracts concepts, proposes triples."""

    name = "scout-arxiv"

    def __init__(self, category: str = "cs.AI", max_papers: int = 50) -> None:
        self.category = category
        self.max_papers = max_papers

    async def run(self, mcp: Client) -> dict[str, Any]:
        log.info("scout.run.start", category=self.category, max=self.max_papers)

        papers = await self._fetch()
        if not papers:
            return {"papers_fetched": 0}

        # In-memory dedup; cross-run dedup falls back to DB UNIQUE constraints
        seen: set[str] = set()
        unique = [p for p in papers if not (p.arxiv_id in seen or seen.add(p.arxiv_id))]

        await self._store_raw(mcp, unique)

        total_mentions = 0
        total_proposals = 0
        papers_with_concepts = 0
        for p in unique:
            concepts = await self._extract_concepts(p)
            if not concepts:
                log.info("scout.paper.no_concepts", arxiv_id=p.arxiv_id)
                continue
            stats = await self._propose_findings(mcp, p, concepts)
            total_mentions += stats["mentions"]
            total_proposals += stats["proposals"]
            papers_with_concepts += 1
            log.info(
                "scout.paper.done",
                arxiv_id=p.arxiv_id, concepts=concepts, **stats,
            )

        summary = {
            "papers_fetched": len(unique),
            "papers_with_concepts": papers_with_concepts,
            "mentions_recorded": total_mentions,
            "triples_proposed": total_proposals,
        }
        log.info("scout.run.done", **summary)
        return summary

    # ----- five-step pipeline (private) -----------------------------------

    async def _fetch(self) -> list[ArxivPaper]:
        url = ARXIV_RSS_TEMPLATE.format(category=self.category)
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(url, headers={"User-Agent": "AgentRadar/0.1"})
            resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        out: list[ArxivPaper] = []
        for entry in feed.entries[: self.max_papers]:
            arxiv_id = entry.id.split(":")[-1]
            published = (
                datetime(*entry.published_parsed[:6], tzinfo=UTC)
                if hasattr(entry, "published_parsed") and entry.published_parsed
                else datetime.now(UTC)
            )
            authors_str = getattr(entry, "author", "")
            authors = [a.strip() for a in authors_str.split(",") if a.strip()]
            out.append(ArxivPaper(
                arxiv_id=arxiv_id,
                title=entry.title.strip(),
                summary=entry.summary.strip(),
                published=published,
                authors=authors,
                link=entry.link,
            ))
        log.info("scout.fetch.done", count=len(out), category=self.category)
        return out

    async def _store_raw(self, mcp: Client, papers: list[ArxivPaper]) -> None:
        for p in papers:
            payload = json.dumps({
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "summary": p.summary,
                "published": p.published.isoformat(),
                "authors": p.authors,
                "link": p.link,
            }, indent=2)
            await mcp.call_tool("put_text_artifact", {
                "key": p.s3_key,
                "content": payload,
                "content_type": "application/json",
            })
        log.info("scout.store_raw.done", count=len(papers))

    async def _extract_concepts(self, paper: ArxivPaper) -> list[str]:
        slm = get_slm_client()
        text = await slm.generate(
            system=EXTRACTION_PROMPT,
            user=f"TITLE: {paper.title}\n\nABSTRACT: {paper.summary}",
            max_tokens=256,
        )
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()
        try:
            parsed = json.loads(text)
            return [c for c in parsed.get("concepts", []) if isinstance(c, str) and c.strip()]
        except json.JSONDecodeError:
            log.warning("scout.extract.bad_json", arxiv_id=paper.arxiv_id, raw=text[:200])
            return []

    async def _propose_findings(
        self, mcp: Client, paper: ArxivPaper, concepts: list[str]
    ) -> dict[str, int]:
        mentions = 0
        proposals = 0
        for concept in concepts:
            await mcp.call_tool("record_mention", {
                "concept_name": concept,
                "source_id": paper.source_id,
                "source_type": "arxiv",
                "observed_at": paper.published.isoformat(),
            })
            mentions += 1
            await mcp.call_tool("propose_triple", {
                "proposer_agent": self.name,
                "subject": concept,
                "predicate": "MENTIONED_IN",
                "object": paper.source_id,
                "source_id": paper.source_id,
                "confidence": 0.6,
            })
            proposals += 1
        return {"mentions": mentions, "proposals": proposals}