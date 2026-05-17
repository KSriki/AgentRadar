"""
Unit tests for trend source adapters.

Each source is tested for: config loading, parsing, error handling.
HTTP is mocked at the httpx layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from agentradar_supervisor.agents.trend_sources.base import TrendItem
from agentradar_supervisor.agents.trend_sources.github import GithubTrendSource
from agentradar_supervisor.agents.trend_sources.hn import HnTrendSource
from agentradar_supervisor.agents.trend_sources.lab_rss import LabRssTrendSource

# ---- TrendItem common shape ----------------------------------------------


class TestTrendItemShape:
    def test_source_id_includes_kind_prefix(self):
        item = TrendItem(
            source_kind="github",
            url="https://github.com/foo/bar",
            title="foo / bar",
            summary="x",
            published_at=datetime.now(UTC),
        )
        assert item.source_id.startswith("trend-github:")

    def test_source_id_stable_for_same_url(self):
        a = TrendItem(
            source_kind="hn",
            url="https://example.com",
            title="x",
            summary="y",
            published_at=datetime.now(UTC),
        )
        b = TrendItem(
            source_kind="hn",
            url="https://example.com",
            title="different",
            summary="different",
            published_at=datetime.now(UTC),
        )
        # source_id only depends on URL, not on title/summary
        assert a.source_id == b.source_id

    def test_s3_key_groups_by_source_kind(self):
        item = TrendItem(
            source_kind="lab_rss",
            url="https://x.com",
            title="x",
            summary="y",
            published_at=datetime.now(UTC),
        )
        assert item.s3_key.startswith("trends/lab_rss/")


# ---- GitHub trend source --------------------------------------------------


GITHUB_API_RESPONSE = {
    "items": [
        {
            "html_url": "https://github.com/anthropics/anthropic-cookbook",
            "full_name": "anthropics/anthropic-cookbook",
            "description": "A collection of notebooks for Claude",
            "stargazers_count": 12345,
            "language": "Python",
            "pushed_at": "2026-04-01T12:00:00Z",
        },
        {
            "html_url": "https://github.com/langchain-ai/langgraph",
            "full_name": "langchain-ai/langgraph",
            "description": "Build resilient language agents as graphs",
            "stargazers_count": 8000,
            "language": "Python",
            "pushed_at": "2026-04-15T08:00:00Z",
        },
    ]
}


class TestGithubTrendSource:
    @pytest.fixture
    def github_config(self, tmp_yaml):
        return tmp_yaml(
            "trend_github.yaml",
            {
                "topics": ["llm-agent", "ai-agents"],
                "since": "weekly",
                "max_per_topic": 5,
            },
        )

    @pytest.mark.asyncio
    async def test_empty_topics_returns_empty(self, tmp_yaml):
        path = tmp_yaml("empty.yaml", {"topics": [], "since": "weekly"})
        src = GithubTrendSource(config_path=path)
        items = await src.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_parses_search_api_response(self, github_config, monkeypatch):
        async def _fake_get(self, url, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.json = MagicMock(return_value=GITHUB_API_RESPONSE)
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        src = GithubTrendSource(config_path=github_config)
        items = await src.fetch()

        # 2 topics × 2 items each = 4
        assert len(items) == 4
        # Each item should be a TrendItem with source_kind=github
        assert all(item.source_kind == "github" for item in items)
        assert all("github.com" in item.url for item in items)

    @pytest.mark.asyncio
    async def test_rate_limit_returns_empty_for_topic(
        self,
        github_config,
        monkeypatch,
    ):
        """A 403 response (rate-limited) should not crash the run."""

        async def _rate_limited(self, url, **kwargs):
            response = MagicMock()
            response.status_code = 403
            response.headers = {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1234567890",
            }
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _rate_limited)

        src = GithubTrendSource(config_path=github_config)
        items = await src.fetch()
        assert items == []  # graceful empty, not exception

    @pytest.mark.asyncio
    async def test_per_topic_failure_isolated(self, github_config, monkeypatch):
        """If topic A fails but topic B succeeds, we get B's items."""
        call_count = [0]

        async def _maybe_fail(self, url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first topic failed")
            response = MagicMock()
            response.status_code = 200
            response.json = MagicMock(return_value=GITHUB_API_RESPONSE)
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _maybe_fail)

        src = GithubTrendSource(config_path=github_config)
        items = await src.fetch()
        # Second topic still produced items
        assert len(items) == 2  # only the second topic's results


# ---- HN trend source ------------------------------------------------------


HN_API_RESPONSE = {
    "hits": [
        {
            "objectID": "12345",
            "url": "https://example.com/agentic-ai-post",
            "title": "Show HN: New agent framework",
            "story_text": "Built this thing for autonomous research agents",
            "points": 150,
            "num_comments": 87,
            "author": "user1",
            "created_at": "2026-04-20T10:00:00Z",
        },
        {
            "objectID": "67890",
            "url": None,  # Some HN posts have no URL (Ask HN, etc.)
            "title": "Ask HN: How are you using LLM agents in production?",
            "story_text": "Curious about real-world deployments...",
            "points": 80,
            "num_comments": 45,
            "author": "user2",
            "created_at": "2026-04-21T14:00:00Z",
        },
    ],
}


class TestHnTrendSource:
    @pytest.fixture
    def hn_config(self, tmp_yaml):
        return tmp_yaml(
            "trend_hn.yaml",
            {
                "keywords": ["AI agent", "LLM agent"],
                "min_points": 30,
                "window_hours": 168,
                "max_results": 15,
            },
        )

    @pytest.mark.asyncio
    async def test_empty_keywords_returns_empty(self, tmp_yaml):
        path = tmp_yaml(
            "empty.yaml", {"keywords": [], "min_points": 30, "window_hours": 168, "max_results": 15}
        )
        src = HnTrendSource(config_path=path)
        assert await src.fetch() == []

    @pytest.mark.asyncio
    async def test_parses_algolia_response(self, hn_config, monkeypatch):
        async def _fake_get(self, url, **kwargs):
            response = MagicMock()
            response.json = MagicMock(return_value=HN_API_RESPONSE)
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        src = HnTrendSource(config_path=hn_config)
        items = await src.fetch()
        assert len(items) == 2
        assert all(item.source_kind == "hn" for item in items)

    @pytest.mark.asyncio
    async def test_falls_back_to_hn_url_when_no_url(self, hn_config, monkeypatch):
        """Ask-HN style posts have no .url; we synthesize from objectID."""
        response_only_no_url = {
            "hits": [HN_API_RESPONSE["hits"][1]]  # the no-URL one
        }

        async def _fake_get(self, url, **kwargs):
            response = MagicMock()
            response.json = MagicMock(return_value=response_only_no_url)
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        src = HnTrendSource(config_path=hn_config)
        items = await src.fetch()
        assert len(items) == 1
        assert "news.ycombinator.com/item?id=67890" in items[0].url

    @pytest.mark.asyncio
    async def test_extra_carries_hn_metadata(self, hn_config, monkeypatch):
        async def _fake_get(self, url, **kwargs):
            response = MagicMock()
            response.json = MagicMock(return_value=HN_API_RESPONSE)
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        src = HnTrendSource(config_path=hn_config)
        items = await src.fetch()
        item = items[0]
        assert item.extra["points"] == 150
        assert item.extra["author"] == "user1"
        assert item.extra["hn_id"] == "12345"

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty(self, hn_config, monkeypatch):
        async def _fail(self, url, **kwargs):
            raise httpx.RequestError("network down")

        monkeypatch.setattr(httpx.AsyncClient, "get", _fail)

        src = HnTrendSource(config_path=hn_config)
        # Should not raise; should return empty
        items = await src.fetch()
        assert items == []


# ---- Lab RSS trend source ------------------------------------------------


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Lab Blog</title>
    <item>
      <title>New Agent Framework Released</title>
      <link>https://lab.example.com/post1</link>
      <description>We released a new framework for building agents.</description>
      <pubDate>Wed, 15 Apr 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Old Post From Last Year</title>
      <link>https://lab.example.com/old-post</link>
      <description>Old content.</description>
      <pubDate>Wed, 15 Apr 2025 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


class TestLabRssTrendSource:
    @pytest.fixture
    def labs_config(self, tmp_yaml):
        return tmp_yaml(
            "trend_labs.yaml",
            {
                "feeds": [
                    {"name": "Test Lab", "url": "https://lab.example.com/rss.xml"},
                ],
                "max_per_feed": 5,
                "window_days": 30,
            },
        )

    @pytest.mark.asyncio
    async def test_empty_feeds_returns_empty(self, tmp_yaml):
        path = tmp_yaml("empty.yaml", {"feeds": [], "max_per_feed": 5, "window_days": 30})
        src = LabRssTrendSource(config_path=path)
        assert await src.fetch() == []

    @pytest.mark.asyncio
    async def test_filters_by_window_days(self, labs_config, monkeypatch):
        """Old entries (outside window_days) should be filtered out."""

        async def _fake_get(self, url, **kwargs):
            response = MagicMock()
            response.text = SAMPLE_RSS
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        # Mock "now" via patching is tricky; instead trust the logic:
        # window_days=30 should drop the 2025 entry, keep 2026 entry
        # (assuming we run this in 2026 or later, which is true given
        # our project's timeline)
        src = LabRssTrendSource(config_path=labs_config)
        items = await src.fetch()
        # Should have at most one (the recent post)
        # If we ran this far in the future this test would break — that's fine
        assert all("post1" in item.url for item in items)

    @pytest.mark.asyncio
    async def test_per_feed_failure_isolated(self, tmp_yaml, monkeypatch):
        path = tmp_yaml(
            "two_feeds.yaml",
            {
                "feeds": [
                    {"name": "Bad", "url": "https://bad.example.com/rss"},
                    {"name": "Good", "url": "https://good.example.com/rss"},
                ],
                "max_per_feed": 5,
                "window_days": 365 * 10,  # huge window so age doesn't filter
            },
        )
        call_count = [0]

        async def _maybe_fail(self, url, **kwargs):
            call_count[0] += 1
            if "bad" in url:
                raise httpx.RequestError("bad feed offline")
            response = MagicMock()
            response.text = SAMPLE_RSS
            response.raise_for_status = MagicMock()
            return response

        monkeypatch.setattr(httpx.AsyncClient, "get", _maybe_fail)

        src = LabRssTrendSource(config_path=path)
        items = await src.fetch()
        # Should still get items from the good feed
        assert len(items) >= 1
        assert all(item.extra.get("lab_name") == "Good" for item in items)
