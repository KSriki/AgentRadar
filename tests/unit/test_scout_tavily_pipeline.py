"""
Unit tests for the Tavily Scout's pipeline.

Same template as arXiv with the differences specific to Tavily:
  - Source IDs are sha256(url)[:32] hashes
  - Confidence is weighted by Tavily's relevance score
  - Source type is 'blog' (Tavily results are open-web prose)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from agentradar_store import TavilyResult
from agentradar_supervisor.agents.scout.tavily import (
    TavilyArtifact,
    TavilyScout,
)


# ---- Helpers --------------------------------------------------------------


def make_result(url: str = "https://example.com/post", **overrides) -> TavilyResult:
    defaults = {
        "url": url,
        "title": "Some Article About MCP",
        "content": "Anthropic announced Model Context Protocol (MCP) for agents.",
        "score": 0.85,
        "published_date": "2026-01-15",
    }
    return TavilyResult(**{**defaults, **overrides})


def make_artifact(url: str = "https://example.com/post", query: str = "test query"):
    return TavilyArtifact(result=make_result(url), query=query)


# ---- Source-ID hashing ----------------------------------------------------


class TestArtifactShape:
    def test_source_id_is_stable_for_same_url(self):
        """Idempotency property: same URL → same source_id every time."""
        a1 = make_artifact("https://example.com/article")
        a2 = make_artifact("https://example.com/article")
        assert a1.source_id == a2.source_id

    def test_source_id_differs_for_different_urls(self):
        a1 = make_artifact("https://example.com/article-1")
        a2 = make_artifact("https://example.com/article-2")
        assert a1.source_id != a2.source_id

    def test_source_id_has_tavily_prefix(self):
        a = make_artifact()
        assert a.source_id.startswith("tavily:")

    def test_source_id_hash_is_truncated_to_32_hex(self):
        a = make_artifact()
        suffix = a.source_id.removeprefix("tavily:")
        assert len(suffix) == 32
        # Should be hex
        int(suffix, 16)  # raises if not valid hex

    def test_s3_key_format(self):
        a = make_artifact()
        assert a.s3_key.startswith("tavily/")
        assert a.s3_key.endswith(".json")


# ---- Concept extraction ---------------------------------------------------


class TestExtractConcepts:
    @pytest.mark.asyncio
    async def test_well_formed_json(self, mock_slm):
        mock_slm.responses = [json.dumps({"concepts": ["MCP", "Anthropic"]})]
        scout = TavilyScout(query="test")
        concepts = await scout._extract_concepts(make_artifact())
        assert concepts == ["MCP", "Anthropic"]

    @pytest.mark.asyncio
    async def test_prompt_uses_title_and_content(self, mock_slm):
        mock_slm.responses = [json.dumps({"concepts": []})]
        scout = TavilyScout(query="test")
        await scout._extract_concepts(make_artifact())
        user_msg = mock_slm.calls[0]["user"]
        assert "TITLE" in user_msg
        assert "CONTENT" in user_msg

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self, mock_slm):
        mock_slm.responses = ["not even json"]
        scout = TavilyScout(query="test")
        assert await scout._extract_concepts(make_artifact()) == []


# ---- Confidence weighting (Tavily-specific) ------------------------------


class TestConfidenceWeighting:
    """Tavily Scout weights propose_triple confidence by Tavily's relevance score."""

    @pytest.mark.asyncio
    async def test_high_relevance_yields_high_confidence(self, mock_mcp):
        scout = TavilyScout(query="test")
        artifact = TavilyArtifact(
            result=make_result(score=1.0), query="test",
        )
        await scout._propose_findings(mock_mcp, artifact, ["X"])
        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_triple"]
        assert len(propose_calls) == 1
        # With score=1.0, confidence should be at the upper bound (0.7)
        assert propose_calls[0]["args"]["confidence"] == pytest.approx(0.7, abs=0.01)

    @pytest.mark.asyncio
    async def test_low_relevance_yields_lower_confidence(self, mock_mcp):
        scout = TavilyScout(query="test")
        artifact = TavilyArtifact(
            result=make_result(score=0.0), query="test",
        )
        await scout._propose_findings(mock_mcp, artifact, ["X"])
        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_triple"]
        # With score=0.0, confidence should be at the floor (0.4)
        assert propose_calls[0]["args"]["confidence"] == pytest.approx(0.4, abs=0.01)

    @pytest.mark.asyncio
    async def test_confidence_is_capped_at_seven_tenths(self, mock_mcp):
        """Even with score=1.0 the cap should be 0.7; we never propose at >0.7."""
        scout = TavilyScout(query="test")
        artifact = TavilyArtifact(
            result=make_result(score=1.0), query="test",
        )
        await scout._propose_findings(mock_mcp, artifact, ["X"])
        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_triple"]
        assert propose_calls[0]["args"]["confidence"] <= 0.7


# ---- Source type ----------------------------------------------------------


class TestSourceTypeIsBlog:
    @pytest.mark.asyncio
    async def test_records_mention_with_blog_source_type(self, mock_mcp):
        scout = TavilyScout(query="test")
        await scout._propose_findings(mock_mcp, make_artifact(), ["X"])
        mention_calls = [c for c in mock_mcp.calls if c["tool"] == "record_mention"]
        assert mention_calls[0]["args"]["source_type"] == "blog"


# ---- Full run() orchestration --------------------------------------------


class TestRunOrchestration:
    @pytest.mark.asyncio
    async def test_no_results_returns_zero_summary(self, mock_mcp, mock_tavily):
        # mock_tavily returns [] by default
        scout = TavilyScout(query="test")
        summary = await scout.run(mock_mcp)
        assert summary["results_fetched"] == 0

    @pytest.mark.asyncio
    async def test_dedupes_by_url(self, mock_mcp, mock_tavily, mock_slm):
        # Two results with same URL — should be deduped
        mock_tavily.search_responses = [[
            make_result("https://x.com/same"),
            make_result("https://x.com/same"),
        ]]
        mock_slm.responses = [json.dumps({"concepts": []})]
        scout = TavilyScout(query="test")
        summary = await scout.run(mock_mcp)
        assert summary["results_fetched"] == 1
        # Only one extraction call
        assert len(mock_slm.calls) == 1

    @pytest.mark.asyncio
    async def test_full_pipeline(self, mock_mcp, mock_tavily, mock_slm):
        mock_tavily.search_responses = [[
            make_result("https://x.com/article-1"),
        ]]
        mock_slm.responses = [json.dumps({"concepts": ["MCP", "AnthropicAgent"]})]

        scout = TavilyScout(query="agent protocols")
        summary = await scout.run(mock_mcp)

        assert summary["results_fetched"] == 1
        assert summary["results_with_concepts"] == 1
        assert summary["mentions_recorded"] == 2
        assert summary["triples_proposed"] == 2

    @pytest.mark.asyncio
    async def test_search_failure_returns_error_summary(
        self, mock_mcp, mock_tavily,
    ):
        # Override the search to raise
        async def _bomb(*args, **kwargs):
            raise RuntimeError("Tavily down")
        mock_tavily.search = _bomb

        scout = TavilyScout(query="test")
        summary = await scout.run(mock_mcp)
        assert summary["results_fetched"] == 0
        assert "error" in summary