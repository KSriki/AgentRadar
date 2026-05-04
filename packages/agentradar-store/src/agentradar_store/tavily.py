"""
Tavily web-search client wrapper.

Tavily is a search/extraction API designed for LLM consumption — you give
it a natural-language query, it returns AI-cleaned content snippets with
source citations. We wrap it as a simple async interface that mirrors the
shape of our other store clients (lazy singleton, healthcheck, structured
return types).

The official tavily-python SDK is sync-only as of writing, so we wrap
its calls in asyncio.to_thread to keep the rest of our async code
non-blocking. If the SDK ever ships native async, this becomes a
one-line swap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tavily import TavilyClient

from agentradar_core import TavilySettings, get_logger, settings

log = get_logger(__name__)


@dataclass(frozen=True)
class TavilyResult:
    """One search result from Tavily."""

    url: str
    title: str
    content: str        # AI-cleaned snippet, NOT the raw HTML
    score: float        # Tavily's relevance score, 0-1
    published_date: str | None = None


class TavilyResearchClient:
    """Thin async wrapper over the sync tavily-python SDK."""

    def __init__(self, cfg: TavilySettings) -> None:
        self._cfg = cfg
        api_key = cfg.api_key.get_secret_value()
        if not api_key:
            raise ValueError(
                "TAVILY_API_KEY is not set. Add it to .env or skip the Tavily Scout."
            )
        self._client = TavilyClient(api_key=api_key)

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        search_depth: str | None = None,
    ) -> list[TavilyResult]:
        """
        Run one search query. Returns a list of cleaned, ranked results.

        We keep this minimal — Tavily's API has more knobs (date filters,
        domain include/exclude, raw-content vs snippet) but the Scout's
        needs are simple and we can add features when they're warranted.
        """
        depth = search_depth or self._cfg.search_depth
        n = max_results or self._cfg.max_results

        # to_thread because tavily-python is sync. The TavilyClient
        # itself uses requests under the hood, so we don't lose any
        # parallelism by wrapping — we just yield to the event loop
        # while the request is in flight.
        try:
            raw = await asyncio.to_thread(
                self._client.search,
                query=query,
                search_depth=depth,
                max_results=n,
                include_answer=False,        # we don't need the LLM-summary
                include_raw_content=False,   # snippets are enough
            )
        except Exception as exc:
            log.warning("tavily.search_failed", query=query, error=str(exc))
            raise

        results = [
            TavilyResult(
                url=r["url"],
                title=r.get("title", ""),
                content=r.get("content", ""),
                score=float(r.get("score", 0.0)),
                published_date=r.get("published_date"),
            )
            for r in raw.get("results", [])
        ]
        log.info(
            "tavily.search_done",
            query=query,
            n_results=len(results),
            depth=depth,
        )
        return results

    async def healthcheck(self) -> bool:
        """Try a trivial query. Used by the api /health endpoint."""
        try:
            await self.search("test", max_results=1, search_depth="basic")
            return True
        except Exception as exc:
            log.warning("tavily.healthcheck_failed", error=str(exc))
            return False


# ---- module-level singleton -----------------------------------------------

_singleton: TavilyResearchClient | None = None


def get_tavily_client() -> TavilyResearchClient:
    global _singleton
    if _singleton is None:
        _singleton = TavilyResearchClient(settings.tavily)
    return _singleton