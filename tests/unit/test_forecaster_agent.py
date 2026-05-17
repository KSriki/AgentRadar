"""
Unit tests for the Forecaster agent class.

Tests cover:
  - Candidate selection (auto-pick via _select_candidate)
  - Forced-concept mode (constructor argument)
  - MCP persistence call shape
  - Behavior when no candidate is available
  - Behavior when ROMA produces no final_forecast

The ROMA graph and Postgres are mocked. The Forecaster's job is
orchestration — pick concept → invoke graph → persist result.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from agentradar_supervisor.agents.forecaster import Forecaster

# ---- Helpers -------------------------------------------------------------


def _final_state_with_forecast(concept_name: str = "MCP", band: str = "medium"):
    """Build a fake ROMA final-state dict with a populated forecast."""
    return {
        "final_forecast": {
            "concept_name": concept_name,
            "prediction": "Test prediction text",
            "confidence": 0.65,
            "horizon_months": 6,
            "reasoning": "Multi-source signal",
            "cited_concept_ids": ["A"],
            "evidence_snapshot": {"total_mentions": 10},
        },
        "confidence_band": band,
    }


def _patch_roma(monkeypatch, return_state):
    """Replace get_roma_graph() with a mock graph whose ainvoke returns the given state."""
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=return_state)
    monkeypatch.setattr(
        "agentradar_supervisor.agents.forecaster.get_roma_graph",
        lambda: mock_graph,
    )
    return mock_graph


def _queue_candidate_response(mcp, concept_name: str | None) -> None:
    """Queue the MCP response that the Forecaster's auto-select call expects."""
    # The mock has its own queueing — push a select_forecast_candidate response
    mcp.responses.setdefault("select_forecast_candidate", []).append({"concept_name": concept_name})


# ---- Candidate selection ------------------------------------------------


class TestCandidateSelection:
    @pytest.mark.asyncio
    async def test_forced_concept_skips_selection(self, mock_mcp, monkeypatch):
        """When concept_name is given to __init__, select_forecast_candidate
        is never called."""
        _patch_roma(monkeypatch, _final_state_with_forecast("ForcedConcept"))

        agent = Forecaster(concept_name="ForcedConcept")
        summary = await agent.run(mock_mcp)
        assert summary["forecasts_produced"] == 1
        assert summary["concept"] == "ForcedConcept"

        # Verify select_forecast_candidate was NOT called
        select_calls = [c for c in mock_mcp.calls if c["tool"] == "select_forecast_candidate"]
        assert len(select_calls) == 0

    @pytest.mark.asyncio
    async def test_auto_select_picks_db_candidate(self, mock_mcp, monkeypatch):
        _queue_candidate_response(mock_mcp, "AutoConcept")
        _patch_roma(monkeypatch, _final_state_with_forecast("AutoConcept"))

        agent = Forecaster()  # no concept_name → auto-select via MCP
        summary = await agent.run(mock_mcp)
        assert summary["concept"] == "AutoConcept"
        assert summary["forecasts_produced"] == 1

        # Verify select_forecast_candidate WAS called
        select_calls = [c for c in mock_mcp.calls if c["tool"] == "select_forecast_candidate"]
        assert len(select_calls) == 1

    @pytest.mark.asyncio
    async def test_no_candidate_returns_zero_forecasts(self, mock_mcp, monkeypatch):
        """Empty DB → select_forecast_candidate returns null → zero forecasts."""
        _queue_candidate_response(mock_mcp, None)

        agent = Forecaster()
        summary = await agent.run(mock_mcp)
        assert summary["forecasts_produced"] == 0
        # propose_forecast should not have been called
        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_forecast"]
        assert len(propose_calls) == 0


# ---- Persistence call shape ---------------------------------------------


class TestPersistenceCall:
    @pytest.mark.asyncio
    async def test_propose_forecast_called_with_correct_fields(
        self,
        mock_mcp,
        monkeypatch,
    ):
        _patch_roma(monkeypatch, _final_state_with_forecast("MCP"))

        agent = Forecaster(concept_name="MCP")
        await agent.run(mock_mcp)

        propose_calls = [c for c in mock_mcp.calls if c["tool"] == "propose_forecast"]
        assert len(propose_calls) == 1
        args = propose_calls[0]["args"]
        assert args["concept_name"] == "MCP"
        assert args["claim"]
