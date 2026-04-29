"""
arXiv Scout — standalone runnable.

Five-step pipeline:
    1. fetch    — pull recent papers from arXiv RSS for one category
    2. dedupe   — skip papers we've already seen (idempotency)
    3. store    — dump raw abstracts to S3 via MCP put_text_artifact
    4. extract  — SLM call: pull candidate concept names from title + abstract
    5. propose  — call MCP record_mention + propose_triple for each (paper, concept)

Run:
    docker compose up -d                      # ensure stack is up
    uv run python scripts/scout_arxiv.py      # default: cs.AI, last 50 papers
    uv run python scripts/scout_arxiv.py --category cs.LG --max 25

The SLM call uses Claude Haiku via Bedrock by default. Set
BEDROCK_SCOUT_MODEL_ID in .env to swap models.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from fastmcp import Client

from agentradar_core import (
    bind_trace_id,
    configure_logging,
    get_logger,
    settings,
)

configure_logging()
log = get_logger("scout.arxiv")


# Defaults — override via CLI flags
DEFAULT_CATEGORY = "cs.AI"
DEFAULT_MAX_PAPERS = 50
ARXIV_RSS_TEMPLATE = "https://export.arxiv.org/rss/{category}"
MCP_URL = "http://localhost:8000/mcp/"

# SLM for concept extraction — small model is the right call for this narrow task
# SCOUT_MODEL_ID = os.getenv(
#     "BEDROCK_SCOUT_MODEL_ID",
#     "anthropic.claude-haiku-4-5-20251001-v1:0",
# )


# ---------------------------------------------------------------------------
# Step 1-2: fetch + dedupe (deterministic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArxivPaper:
    arxiv_id: str         # e.g., "2401.12345"
    title: str
    summary: str          # the abstract
    published: datetime
    authors: list[str]
    link: str

    @property
    def source_id(self) -> str:
        """Stable identifier for use as Source.id in the graph."""
        return f"arxiv:{self.arxiv_id}"

    @property
    def s3_key(self) -> str:
        return f"arxiv/{self.arxiv_id}.json"


async def fetch_arxiv(category: str, max_papers: int) -> list[ArxivPaper]:
    """Pull and parse the arXiv RSS feed for a category."""
    url = ARXIV_RSS_TEMPLATE.format(category=category)
    log.info("scout.fetch.start", url=url)

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(url, headers={"User-Agent": "AgentRadar/0.1"})
        resp.raise_for_status()

    feed = feedparser.parse(resp.text)
    papers: list[ArxivPaper] = []
    for entry in feed.entries[:max_papers]:
        # arXiv RSS entry IDs look like "oai:arXiv.org:2401.12345"
        arxiv_id = entry.id.split(":")[-1]
        published = (
            datetime(*entry.published_parsed[:6], tzinfo=UTC)
            if hasattr(entry, "published_parsed") and entry.published_parsed
            else datetime.now(UTC)
        )
        # Authors come as "Foo, Bar, Baz" string in RSS; arXiv API has them
        # structured but RSS is simpler. Good enough for now.
        authors_str = getattr(entry, "author", "")
        authors = [a.strip() for a in authors_str.split(",") if a.strip()]

        papers.append(
            ArxivPaper(
                arxiv_id=arxiv_id,
                title=entry.title.strip(),
                summary=entry.summary.strip(),
                published=published,
                authors=authors,
                link=entry.link,
            )
        )
    log.info("scout.fetch.done", count=len(papers), category=category)
    return papers


async def filter_unseen(
    mcp: Client, papers: list[ArxivPaper]
) -> list[ArxivPaper]:
    """
    Skip papers we've already recorded a mention for. The mentions table is
    UNIQUE on (concept_name, source_id), so re-recording is safe but wasteful.
    Quick check: if the paper appears in any mention, treat as seen.

    Note: this is a placeholder. A real implementation would use a dedicated
    `seen_artifacts` tracking table. For now, we'll let the idempotent
    record_mention handle dedup and just dedupe in-memory within this run.
    """
    # In-memory dedup is enough for a single run. Cross-run dedup falls
    # back to the database UNIQUE constraints, which is the proper place.
    seen: set[str] = set()
    unique: list[ArxivPaper] = []
    for p in papers:
        if p.arxiv_id in seen:
            continue
        seen.add(p.arxiv_id)
        unique.append(p)
    if len(unique) < len(papers):
        log.info("scout.dedupe", in_run_dupes=len(papers) - len(unique))
    return unique


# ---------------------------------------------------------------------------
# Step 3: store raw artifacts (deterministic, via MCP)
# ---------------------------------------------------------------------------


async def store_raw_artifacts(
    mcp: Client, papers: list[ArxivPaper]
) -> dict[str, str]:
    """Dump each paper's metadata + abstract to S3. Returns {arxiv_id: s3_uri}."""
    uris: dict[str, str] = {}
    for p in papers:
        payload = json.dumps(
            {
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "summary": p.summary,
                "published": p.published.isoformat(),
                "authors": p.authors,
                "link": p.link,
            },
            indent=2,
        )
        result = await mcp.call_tool(
            "put_text_artifact",
            {
                "key": p.s3_key,
                "content": payload,
                "content_type": "application/json",
            },
        )
        uris[p.arxiv_id] = result.data["uri"]
    log.info("scout.store_raw.done", count=len(uris))
    return uris


# ---------------------------------------------------------------------------
# Step 4: SLM-based concept extraction (the only LLM call)
# ---------------------------------------------------------------------------


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
- If no concepts are present, return an empty list — don't guess.

Respond ONLY with valid JSON in this shape, no prose, no markdown fences:
{"concepts": ["ConceptA", "ConceptB"]}
"""


async def extract_concepts_slm(paper: ArxivPaper) -> list[str]:
    """
    Use the configured SLM (Ollama locally, Bedrock in cloud) to pull
    candidate concepts from one paper. The provider switch is in .env.
    """
    from agentradar_store import get_slm_client

    user_content = f"TITLE: {paper.title}\n\nABSTRACT: {paper.summary}"

    slm = get_slm_client()
    text = await slm.generate(
        system=EXTRACTION_PROMPT,
        user=user_content,
        max_tokens=256,
    )

    # Strip common JSON-fencing patterns the smaller models sometimes emit
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()

    try:
        parsed = json.loads(text)
        concepts = parsed.get("concepts", [])
        return [c for c in concepts if isinstance(c, str) and c.strip()]
    except json.JSONDecodeError:
        log.warning("scout.extract.bad_json", arxiv_id=paper.arxiv_id, raw=text[:200])
        return []


# ---------------------------------------------------------------------------
# Step 5: propose mentions + triples (deterministic, via MCP)
# ---------------------------------------------------------------------------


async def propose_findings(
    mcp: Client,
    paper: ArxivPaper,
    concepts: list[str],
) -> dict[str, int]:
    """
    For each extracted concept:
      - record a mention (paper, concept, arxiv, observed_at)
      - propose a MENTIONED_IN triple to the pending queue
    """
    mentions = 0
    proposals = 0
    for concept in concepts:
        # 1. Mention (idempotent on (concept, source_id))
        await mcp.call_tool(
            "record_mention",
            {
                "concept_name": concept,
                "source_id": paper.source_id,
                "source_type": "arxiv",
                "observed_at": paper.published.isoformat(),
            },
        )
        mentions += 1

        # 2. Propose a MENTIONED_IN triple — Critic decides whether to commit
        await mcp.call_tool(
            "propose_triple",
            {
                "proposer_agent": "scout-arxiv",
                "subject": concept,
                "predicate": "MENTIONED_IN",
                "object": paper.source_id,
                "source_id": paper.source_id,
                "confidence": 0.6,  # medium — Critic should validate via faithfulness
            },
        )
        proposals += 1

    return {"mentions": mentions, "proposals": proposals}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_scout(category: str, max_papers: int) -> dict[str, Any]:
    """End-to-end Scout invocation. Returns summary stats."""
    trace_id = hashlib.sha256(
        f"scout-arxiv-{category}-{datetime.now(UTC).isoformat()}".encode()
    ).hexdigest()[:16]
    bind_trace_id(trace_id)

    log.info("scout.run.start", category=category, max=max_papers)

    # Step 1: fetch
    papers = await fetch_arxiv(category, max_papers)
    if not papers:
        log.warning("scout.run.no_papers")
        return {"papers": 0}

    async with Client(MCP_URL) as mcp:
        # Step 2: dedupe (in-memory + DB-side via UNIQUE constraints)
        papers = await filter_unseen(mcp, papers)

        # Step 3: store raw artifacts to S3
        await store_raw_artifacts(mcp, papers)

        # Step 4 + 5: for each paper, extract concepts and propose
        total_mentions = 0
        total_proposals = 0
        papers_with_concepts = 0
        for p in papers:
            concepts = await extract_concepts_slm(p)
            if not concepts:
                log.info("scout.paper.no_concepts", arxiv_id=p.arxiv_id)
                continue
            stats = await propose_findings(mcp, p, concepts)
            total_mentions += stats["mentions"]
            total_proposals += stats["proposals"]
            papers_with_concepts += 1
            log.info(
                "scout.paper.done",
                arxiv_id=p.arxiv_id,
                concepts=concepts,
                **stats,
            )

    summary = {
        "papers_fetched": len(papers),
        "papers_with_concepts": papers_with_concepts,
        "mentions_recorded": total_mentions,
        "triples_proposed": total_proposals,
        "trace_id": trace_id,
    }
    log.info("scout.run.done", **summary)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="arXiv Scout for AgentRadar.")
    parser.add_argument(
        "--category", default=DEFAULT_CATEGORY,
        help=f"arXiv category (default: {DEFAULT_CATEGORY})",
    )
    parser.add_argument(
        "--max", type=int, default=DEFAULT_MAX_PAPERS,
        help=f"Max papers per run (default: {DEFAULT_MAX_PAPERS})",
    )
    args = parser.parse_args()

    summary = asyncio.run(run_scout(args.category, args.max))
    print()
    print("=" * 60)
    print("Scout run complete")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:25} {v}")


if __name__ == "__main__":
    main()