"""
Lab and vendor RSS feed source.

Pulls from a configured set of first-party feeds (Anthropic, OpenAI,
Google AI, DeepMind, LangChain). Uses feedparser, same library
already in use for arXiv RSS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import feedparser
import httpx
import yaml
from agentradar_core import get_logger

from agentradar_supervisor.agents.trend_sources.base import TrendItem

log = get_logger(__name__)


class LabRssTrendSource:
    """Pulls recent entries from a configured set of lab RSS feeds."""

    name = "lab_rss"
    USER_AGENT = "AgentRadar/0.1"

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path.cwd() / "config" / "scouts" / "trend_labs.yaml"
        cfg = yaml.safe_load(config_path.read_text())
        self.feeds: list[dict[str, str]] = list(cfg.get("feeds", []))
        self.max_per_feed: int = int(cfg.get("max_per_feed", 5))
        self.window_days: int = int(cfg.get("window_days", 14))

    async def fetch(self) -> list[TrendItem]:
        if not self.feeds:
            log.warning("trend.lab_rss.no_feeds_configured")
            return []

        cutoff = datetime.now(UTC) - timedelta(days=self.window_days)
        all_items: list[TrendItem] = []

        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": self.USER_AGENT}, follow_redirects=True
        ) as http:
            for feed in self.feeds:
                try:
                    items = await self._fetch_feed(http, feed, cutoff)
                    all_items.extend(items)
                except Exception as exc:
                    log.warning(
                        "trend.lab_rss.feed_failed",
                        feed=feed.get("name"),
                        url=feed.get("url"),
                        error=str(exc),
                    )

        log.info("trend.lab_rss.fetched", total=len(all_items))
        return all_items

    async def _fetch_feed(
        self,
        http: httpx.AsyncClient,
        feed_cfg: dict[str, str],
        cutoff: datetime,
    ) -> list[TrendItem]:
        url = feed_cfg["url"]
        name = feed_cfg.get("name", url)

        resp = await http.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)

        items: list[TrendItem] = []
        for entry in parsed.entries[: self.max_per_feed * 3]:  # over-fetch, then filter
            # Published date — feeds vary in field name; feedparser normalizes
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=UTC)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=UTC)
            else:
                published = datetime.now(UTC)

            if published < cutoff:
                continue

            title = (getattr(entry, "title", "") or "").strip()
            summary = (
                getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            ).strip()
            link = getattr(entry, "link", "").strip()
            if not link or not title:
                continue

            items.append(
                TrendItem(
                    source_kind="lab_rss",
                    url=link,
                    title=title,
                    summary=summary,
                    published_at=published,
                    extra={"lab_name": name, "feed_url": url},
                )
            )
            if len(items) >= self.max_per_feed:
                break

        log.info("trend.lab_rss.feed_fetched", name=name, count=len(items))
        return items
