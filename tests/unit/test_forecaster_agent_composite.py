"""
Unit tests for Forecaster.run_topn and Forecaster.run_digest.

The ROMA graph is mocked — these tests verify orchestration logic
(passing the right initial state, persisting the digest, summarizing
the result) without exercising the underlying nodes or SLM calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentradar_supervisor.agents.forecaster import Forecaster


def _patch_graph(monkeypatch, final_state):
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=final_state)
    monkeypatch.setattr(
        "agentradar_supervisor.agents.forecaster.get_roma_graph",
        lambda: mock_graph,
    )
    return mock_graph


# ---- run_topn -----------------------------------------------------------


class TestRunTopn:
    @pytest.mark.asyncio
    async def test_run_topn_returns_concept_summary(self, mock_mcp, monkeypatch):
        _patch_graph(monkeypatch, {
            "final_topn": [
                {"concept_name": "MCP", "confidence": 0.7},
                {"concept_name": "A2A", "confidence": 0.5},
                {"concept_name": "ROMA", "confidence": 0.8},
            ],
        })
        agent = Forecaster()
        summary = await agent.run_topn(mock_mcp, top_n=3)
        assert summary["forecasts_produced"] == 3
        assert summary["concepts"] == ["MCP", "A2A", "ROMA"]

    @pytest.mark.asyncio
    async def test_run_topn_empty_result(self, mock_mcp, monkeypatch):
        _patch_graph(monkeypatch, {"final_topn": []})
        agent = Forecaster()
        summary = await agent.run_topn(mock_mcp, top_n=5)
        assert summary["forecasts_produced"] == 0
        assert summary["concepts"] == []

    @pytest.mark.asyncio
    async def test_run_topn_passes_top_n_to_graph(self, mock_mcp, monkeypatch):
        """Verify the initial state given to the graph has the right top_n."""
        mock_graph = _patch_graph(monkeypatch, {"final_topn": []})
        agent = Forecaster()
        await agent.run_topn(mock_mcp, top_n=7)
        called_state = mock_graph.ainvoke.call_args[0][0]
        assert called_state["task"]["kind"] == "forecast.top_n"
        assert called_state["task"]["top_n"] == 7
        assert called_state["depth"] == 0
        assert "mcp" in called_state


# ---- run_digest ---------------------------------------------------------


class TestRunDigest:
    @pytest.mark.asyncio
    async def test_run_digest_persists_via_mcp(self, mock_mcp, monkeypatch):
        _patch_graph(monkeypatch, {
            "final_digest": {
                "label": "weekly",
                "themes": "test themes",
                "standout": "test standout",
                "forecasts": [{"concept_name": "MCP"}, {"concept_name": "A2A"}],
                "average_confidence": 0.65,
            },
            "confidence_band": "medium",
        })
        agent = Forecaster()
        summary = await agent.run_digest(mock_mcp, top_n=5, label="weekly")
        # Verify the MCP propose_digest call happened with right args
        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_digest"]
        assert len(propose_calls) == 1
        args = propose_calls[0]["args"]
        assert args["label"] == "weekly"
        assert args["themes"] == "test themes"
        assert args["average_confidence"] == 0.65
        assert args["confidence_band"] == "medium"
        assert summary["digests_produced"] == 1
        assert summary["forecasts_count"] == 2

    @pytest.mark.asyncio
    async def test_run_digest_default_label_when_none(self, mock_mcp, monkeypatch):
        """When label is None, the agent generates a date-stamped default."""
        _patch_graph(monkeypatch, {
            "final_digest": {
                "label": "",  # graph saw the actual generated label
                "themes": "t", "standout": "s",
                "forecasts": [{"concept_name": "MCP"}],
                "average_confidence": 0.5,
            },
            "confidence_band": "medium",
        })
        agent = Forecaster()
        await agent.run_digest(mock_mcp, top_n=3, label=None)
        # Verify the graph was given a non-empty label
        from unittest.mock import call
        mock_graph_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_digest"]
        assert len(mock_graph_calls) == 1

    @pytest.mark.asyncio
    async def test_run_digest_zero_forecasts_returns_zero(self, mock_mcp, monkeypatch):
        _patch_graph(monkeypatch, {
            "final_digest": {"forecasts": []},
            "confidence_band": "weak",
        })
        agent = Forecaster()
        summary = await agent.run_digest(mock_mcp, top_n=5)
        assert summary["digests_produced"] == 0
        # propose_digest should NOT have been called
        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_digest"]
        assert len(propose_calls) == 0

    @pytest.mark.asyncio
    async def test_run_digest_missing_final_digest_returns_zero(self, mock_mcp, monkeypatch):
        """If the graph returned without final_digest, defensive zero."""
        _patch_graph(monkeypatch, {"confidence_band": "weak"})
        agent = Forecaster()
        summary = await agent.run_digest(mock_mcp, top_n=5)
        assert summary["digests_produced"] == 0