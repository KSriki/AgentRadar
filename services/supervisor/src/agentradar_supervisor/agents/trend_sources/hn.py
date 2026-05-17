"""
Hacker News trend source via the Algolia HN API.

Uses Algolia's free HN search API (no auth, no rate limits at our
cadence) to pull recent high-score stories matching agentic-AI
keywords.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml
from agentradar_core import get_logger

from agentradar_supervisor.agents.trend_sources.base import TrendItem

log = get_logger(__name__)


class HnTrendSource:
    """Fetches high-score HN stories matching agent-related keywords."""

    name = "hn"
    API_URL = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path.cwd() / "config" / "scouts" / "trend_hn.yaml"
        cfg = yaml.safe_load(config_path.read_text())
        self.keywords: list[str] = list(cfg.get("keywords", []))
        self.min_points: int = int(cfg.get("min_points", 30))
        self.window_hours: int = int(cfg.get("window_hours", 168))
        self.max_results: int = int(cfg.get("max_results", 15))

    async def fetch(self) -> list[TrendItem]:
        if not self.keywords:
            log.warning("trend.hn.no_keywords_configured")
            return []

        # Algolia accepts OR'd terms via parentheses — we wrap each
        # keyword in quotes for phrase matching.
        query = " OR ".join(f'"{kw}"' for kw in self.keywords)
        cutoff = int((datetime.now(UTC) - timedelta(hours=self.window_hours)).timestamp())

        params = {
            "query": query,
            "tags": "story",
            # numericFilters: comma-separated list of numeric predicates
            "numericFilters": f"points>={self.min_points},created_at_i>={cutoff}",
            "hitsPerPage": str(self.max_results),
        }

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            try:
                resp = await http.get(self.API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("trend.hn.fetch_failed", error=str(exc))
                return []

        items: list[TrendItem] = []
        for hit in data.get("hits", []):
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            # Stories don't always have a body; HN's value is the title + comments
            story_text = (hit.get("story_text") or "").strip()
            summary = story_text or title  # fall back to title

            published_iso = hit.get("created_at", "")
            try:
                published_at = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
            except ValueError:
                published_at = datetime.now(UTC)

            items.append(
                TrendItem(
                    source_kind="hn",
                    url=url,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    extra={
                        "hn_id": hit.get("objectID"),
                        "points": hit.get("points"),
                        "num_comments": hit.get("num_comments"),
                        "author": hit.get("author"),
                    },
                )
            )

        log.info("trend.hn.fetched", count=len(items), query=query[:80])
        return items
