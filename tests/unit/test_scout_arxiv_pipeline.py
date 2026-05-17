"""
Unit tests for the arXiv Scout's pipeline.

Mocks: HTTP (feedparser input), MCP, SLM. Tests cover:
  - RSS parsing handles well-formed and malformed entries
  - In-memory dedup
  - SLM concept extraction with valid, malformed, and fenced JSON
  - The full run() orchestration with structured assertions on what
    MCP tools were called and with what payloads
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from agentradar_supervisor.agents.scout.arxiv import (
    ArxivPaper,
    ArxivScout,
)

# ---- Helpers --------------------------------------------------------------


def make_paper(arxiv_id: str = "2401.12345", **overrides) -> ArxivPaper:
    defaults = {
        "arxiv_id": arxiv_id,
        "title": "Test Paper Title",
        "summary": "Abstract about MCP and LangGraph and agent things.",
        "published": datetime(2026, 1, 1, tzinfo=UTC),
        "authors": ["A. Author", "B. Author"],
        "link": f"https://arxiv.org/abs/{arxiv_id}",
    }
    return ArxivPaper(**{**defaults, **overrides})


# ---- Source ID and S3 key derivation -------------------------------------


class TestArxivPaperShape:
    """ArxivPaper's derived properties (source_id, s3_key)."""

    def test_source_id_format(self):
        paper = make_paper("2401.12345")
        assert paper.source_id == "arxiv:2401.12345"

    def test_s3_key_format(self):
        paper = make_paper("2401.12345")
        assert paper.s3_key == "arxiv/2401.12345.json"

    def test_arxiv_paper_is_immutable(self):
        """Frozen dataclass — should reject mutation."""
        paper = make_paper()
        with pytest.raises(FrozenInstanceError):
            paper.arxiv_id = "different"  # type: ignore


# ---- Concept extraction ---------------------------------------------------


class TestExtractConcepts:
    """SLM-driven concept extraction with various model output shapes."""

    @pytest.mark.asyncio
    async def test_well_formed_json_extracts_concepts(self, mock_slm):
        mock_slm.responses = [json.dumps({"concepts": ["MCP", "LangGraph"]})]
        scout = ArxivScout(category="cs.AI", max_papers=1)
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == ["MCP", "LangGraph"]

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_handled(self, mock_slm):
        """Smaller models often emit ```json ... ``` despite instructions."""
        mock_slm.responses = ['```json\n{"concepts": ["MCP", "ReAct"]}\n```']
        scout = ArxivScout()
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == ["MCP", "ReAct"]

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty_list(self, mock_slm):
        mock_slm.responses = ["this is not json {broken"]
        scout = ArxivScout()
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == []

    @pytest.mark.asyncio
    async def test_empty_concepts_list_returns_empty(self, mock_slm):
        """SLM saying 'no concepts found' yields empty list, not an error."""
        mock_slm.responses = [json.dumps({"concepts": []})]
        scout = ArxivScout()
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == []

    @pytest.mark.asyncio
    async def test_filters_non_string_entries(self, mock_slm):
        """If SLM hallucinates non-string items in the array, drop them."""
        mock_slm.responses = [json.dumps({"concepts": ["GoodOne", None, 42, "AnotherGood", ""]})]
        scout = ArxivScout()
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == ["GoodOne", "AnotherGood"]

    @pytest.mark.asyncio
    async def test_filters_whitespace_only_entries(self, mock_slm):
        mock_slm.responses = [json.dumps({"concepts": ["MCP", "   ", "\t", "LangGraph"]})]
        scout = ArxivScout()
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == ["MCP", "LangGraph"]

    @pytest.mark.asyncio
    async def test_missing_concepts_key_returns_empty(self, mock_slm):
        mock_slm.responses = [json.dumps({"other_field": "stuff"})]
        scout = ArxivScout()
        concepts = await scout._extract_concepts(make_paper())
        assert concepts == []

    @pytest.mark.asyncio
    async def test_prompt_includes_title_and_abstract(self, mock_slm):
        """Verify the SLM is given both title and abstract for context."""
        mock_slm.responses = [json.dumps({"concepts": []})]
        scout = ArxivScout()
        await scout._extract_concepts(
            make_paper(title="Specific Title", summary="Specific Abstract")
        )
        assert len(mock_slm.calls) == 1
        user_msg = mock_slm.calls[0]["user"]
        assert "Specific Title" in user_msg
        assert "Specific Abstract" in user_msg


# ---- Proposal of findings -------------------------------------------------


class TestProposeFindings:
    """Per-paper, per-concept MCP calls for record_mention + propose_triple."""

    @pytest.mark.asyncio
    async def test_records_mention_per_concept(self, mock_mcp):
        scout = ArxivScout()
        paper = make_paper()
        await scout._propose_findings(mock_mcp, paper, ["MCP", "ReAct"])

        mention_calls = [c for c in mock_mcp.calls if c["tool"] == "record_mention"]
        assert len(mention_calls) == 2
        # Each call should reference the paper as source
        assert all(c["args"]["source_id"] == paper.source_id for c in mention_calls)
        assert {c["args"]["concept_name"] for c in mention_calls} == {"MCP", "ReAct"}

    @pytest.mark.asyncio
    async def test_proposes_triple_per_concept(self, mock_mcp):
        scout = ArxivScout()
        paper = make_paper()
        await scout._propose_findings(mock_mcp, paper, ["MCP"])

        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_triple"]
        assert len(propose_calls) == 1
        args = propose_calls[0]["args"]
        assert args["subject"] == "MCP"
        assert args["predicate"] == "MENTIONED_IN"
        assert args["object"] == paper.source_id
        assert args["proposer_agent"] == "scout-arxiv"

    @pytest.mark.asyncio
    async def test_proposal_uses_arxiv_source_type(self, mock_mcp):
        scout = ArxivScout()
        paper = make_paper()
        await scout._propose_findings(mock_mcp, paper, ["MCP"])

        mention_calls = [c for c in mock_mcp.calls if c["tool"] == "record_mention"]
        assert mention_calls[0]["args"]["source_type"] == "arxiv"

    @pytest.mark.asyncio
    async def test_returns_correct_counts(self, mock_mcp):
        scout = ArxivScout()
        stats = await scout._propose_findings(mock_mcp, make_paper(), ["A", "B", "C"])
        assert stats == {"mentions": 3, "proposals": 3}

    @pytest.mark.asyncio
    async def test_empty_concepts_makes_no_calls(self, mock_mcp):
        scout = ArxivScout()
        stats = await scout._propose_findings(mock_mcp, make_paper(), [])
        assert stats == {"mentions": 0, "proposals": 0}
        assert len(mock_mcp.calls) == 0


# ---- Full run() orchestration ---------------------------------------------


class TestRunOrchestration:
    """Test the public Agent.run interface end-to-end with mocked layers."""

    @pytest.mark.asyncio
    async def test_empty_fetch_returns_zero_summary(self, mock_mcp):
        scout = ArxivScout()
        with patch.object(scout, "_fetch", AsyncMock(return_value=[])):
            summary = await scout.run(mock_mcp)
        assert summary == {"papers_fetched": 0}

    @pytest.mark.asyncio
    async def test_dedupes_in_memory_by_arxiv_id(self, mock_mcp, mock_slm):
        """Two papers with the same arxiv_id should be processed once."""
        mock_slm.responses = [json.dumps({"concepts": []})]  # one extraction call expected

        scout = ArxivScout()
        dup_papers = [
            make_paper("2401.00001"),
            make_paper("2401.00001"),  # exact duplicate
        ]
        with patch.object(scout, "_fetch", AsyncMock(return_value=dup_papers)):
            summary = await scout.run(mock_mcp)

        # Only one paper should have been processed for SLM extraction
        assert summary["papers_fetched"] == 1
        assert len(mock_slm.calls) == 1

    @pytest.mark.asyncio
    async def test_paper_with_no_concepts_does_not_propose(
        self,
        mock_mcp,
        mock_slm,
    ):
        """SLM returns empty concepts → no record_mention or propose_triple."""
        mock_slm.responses = [json.dumps({"concepts": []})]

        scout = ArxivScout()
        with patch.object(scout, "_fetch", AsyncMock(return_value=[make_paper()])):
            summary = await scout.run(mock_mcp)

        assert summary["mentions_recorded"] == 0
        assert summary["triples_proposed"] == 0
        # Only the put_text_artifact call should have happened
        proposal_calls = [
            c for c in mock_mcp.calls if c["tool"] in ("record_mention", "propose_triple")
        ]
        assert len(proposal_calls) == 0

    @pytest.mark.asyncio
    async def test_full_pipeline_with_concepts(self, mock_mcp, mock_slm):
        """Happy path: fetch → store → extract → propose for one paper."""
        mock_slm.responses = [json.dumps({"concepts": ["MCP", "ReAct"]})]

        scout = ArxivScout()
        with patch.object(scout, "_fetch", AsyncMock(return_value=[make_paper()])):
            summary = await scout.run(mock_mcp)

        assert summary["papers_fetched"] == 1
        assert summary["papers_with_concepts"] == 1
        assert summary["mentions_recorded"] == 2
        assert summary["triples_proposed"] == 2

        # Verify the right MCP tools were called the right number of times
        tool_counts = {}
        for c in mock_mcp.calls:
            tool_counts[c["tool"]] = tool_counts.get(c["tool"], 0) + 1
        assert tool_counts.get("put_text_artifact") == 1  # one artifact stored
        assert tool_counts.get("record_mention") == 2  # two concepts
        assert tool_counts.get("propose_triple") == 2
