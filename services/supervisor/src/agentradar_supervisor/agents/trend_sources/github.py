"""
GitHub trending source — uses GitHub's REST Search API instead of
scraping or Atom feeds (both of which proved unstable).

Approximates "trending" as "topic:<topic> AND pushed in the last N days,
sorted by stars descending." Not identical to GitHub's secret trending
algorithm but a reasonable proxy that returns popular, actively-maintained
repos in each topic.

Anonymous access is 10 requests/minute — well above our cadence (one
request per topic every 6 hours). No PAT required for our scale.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml
from agentradar_core import get_logger

from agentradar_supervisor.agents.trend_sources.base import TrendItem

log = get_logger(__name__)


# Map our config's `since` values to day offsets for the Search query.
# GitHub Search uses `pushed:>=YYYY-MM-DD` filters; we approximate the
# trending time window as "pushed in the last N days."
SINCE_TO_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}


class GithubTrendSource:
    """Approximates trending via the GitHub Search API for popular topics."""

    name = "github"
    SEARCH_URL = "https://api.github.com/search/repositories"
    USER_AGENT = "AgentRadar/0.1"

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path.cwd() / "config" / "scouts" / "trend_github.yaml"
        cfg = yaml.safe_load(config_path.read_text())
        self.topics: list[str] = list(cfg.get("topics", []))
        self.since: str = cfg.get("since", "weekly")
        self.max_per_topic: int = int(cfg.get("max_per_topic", 5))

    async def fetch(self) -> list[TrendItem]:
        if not self.topics:
            log.warning("trend.github.no_topics_configured")
            return []

        days = SINCE_TO_DAYS.get(self.since, 7)
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")

        items: list[TrendItem] = []
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
            follow_redirects=True,
        ) as http:
            for topic in self.topics:
                try:
                    items.extend(await self._fetch_topic(http, topic, cutoff))
                except Exception as exc:
                    # Per-topic isolation — one bad query doesn't kill the run
                    log.warning(
                        "trend.github.topic_failed",
                        topic=topic,
                        error=str(exc),
                    )

        log.info("trend.github.fetched", total=len(items))
        return items

    async def _fetch_topic(
        self,
        http: httpx.AsyncClient,
        topic: str,
        cutoff: str,
    ) -> list[TrendItem]:
        params = {
            "q": f"topic:{topic} pushed:>={cutoff}",
            "sort": "stars",
            "order": "desc",
            "per_page": str(self.max_per_topic),
        }

        resp = await http.get(self.SEARCH_URL, params=params)

        # Anonymous rate-limit hit gets a 403; log explicitly so it's diagnosable
        if resp.status_code == 403:
            log.warning(
                "trend.github.rate_limited",
                topic=topic,
                limit_remaining=resp.headers.get("X-RateLimit-Remaining"),
                reset=resp.headers.get("X-RateLimit-Reset"),
            )
            return []
        resp.raise_for_status()

        data = resp.json()
        items: list[TrendItem] = []
        for repo in data.get("items", []):
            html_url = (repo.get("html_url") or "").strip()
            full_name = (repo.get("full_name") or "").strip()
            if not html_url or not full_name:
                continue

            description = (repo.get("description") or "").strip()
            stars = int(repo.get("stargazers_count") or 0)

            pushed_at_str = repo.get("pushed_at", "")
            try:
                pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pushed_at = datetime.now(UTC)

            items.append(
                TrendItem(
                    source_kind="github",
                    url=html_url,
                    title=full_name.replace("/", " / "),
                    summary=description,
                    published_at=pushed_at,
                    extra={
                        "topic": topic,
                        "repo_path": full_name,
                        "stars": stars,
                        "language": (repo.get("language") or ""),
                        "since": self.since,
                    },
                )
            )

        log.info("trend.github.topic_fetched", topic=topic, count=len(items))
        return items
