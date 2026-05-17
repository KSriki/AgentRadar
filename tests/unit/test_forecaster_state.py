"""
Unit tests for Forecaster state schemas.

Pydantic validation on CandidateForecast pins the structured-output
contract — if the SLM is asked to return JSON in this shape, we verify
the receiving end correctly accepts valid shapes and rejects invalid ones.
"""

from __future__ import annotations

import pytest
from agentradar_supervisor.state import (
    CandidateForecast,
    ForecastState,
    ForecastTask,
)
from pydantic import ValidationError

# ---- CandidateForecast validation ----------------------------------------


class TestCandidateForecast:
    """The Pydantic schema the SLM must populate."""

    def test_valid_complete_payload(self):
        c = CandidateForecast(
            prediction="MCP adoption will continue to grow.",
            confidence=0.7,
            horizon_months=6,
            reasoning="Mentioned across multiple source types.",
            cited_concept_ids=["MCP", "Anthropic"],
        )
        assert c.prediction.startswith("MCP")
        assert c.confidence == 0.7
        assert c.horizon_months == 6

    def test_confidence_at_lower_bound(self):
        c = CandidateForecast(
            prediction="x",
            confidence=0.0,
            reasoning="y",
        )
        assert c.confidence == 0.0

    def test_confidence_at_upper_bound(self):
        c = CandidateForecast(
            prediction="x",
            confidence=1.0,
            reasoning="y",
        )
        assert c.confidence == 1.0

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            CandidateForecast(
                prediction="x",
                confidence=-0.1,
                reasoning="y",
            )

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            CandidateForecast(
                prediction="x",
                confidence=1.1,
                reasoning="y",
            )

    def test_horizon_months_default(self):
        c = CandidateForecast(prediction="x", confidence=0.5, reasoning="y")
        assert c.horizon_months == 6  # the default per the field spec

    def test_horizon_months_lower_bound(self):
        c = CandidateForecast(
            prediction="x",
            confidence=0.5,
            horizon_months=1,
            reasoning="y",
        )
        assert c.horizon_months == 1

    def test_horizon_months_upper_bound(self):
        c = CandidateForecast(
            prediction="x",
            confidence=0.5,
            horizon_months=24,
            reasoning="y",
        )
        assert c.horizon_months == 24

    def test_horizon_zero_rejected(self):
        with pytest.raises(ValidationError):
            CandidateForecast(
                prediction="x",
                confidence=0.5,
                horizon_months=0,
                reasoning="y",
            )

    def test_horizon_too_far_rejected(self):
        with pytest.raises(ValidationError):
            CandidateForecast(
                prediction="x",
                confidence=0.5,
                horizon_months=25,
                reasoning="y",
            )

    def test_cited_concept_ids_defaults_empty(self):
        c = CandidateForecast(prediction="x", confidence=0.5, reasoning="y")
        assert c.cited_concept_ids == []

    def test_missing_prediction_rejected(self):
        with pytest.raises(ValidationError):
            CandidateForecast(confidence=0.5, reasoning="y")

    def test_model_dump_round_trips(self):
        c = CandidateForecast(
            prediction="p",
            confidence=0.5,
            reasoning="r",
            horizon_months=12,
            cited_concept_ids=["X"],
        )
        dumped = c.model_dump()
        restored = CandidateForecast(**dumped)
        assert restored.prediction == c.prediction
        assert restored.cited_concept_ids == c.cited_concept_ids


# ---- ForecastTask / ForecastState shape sanity ---------------------------


class TestForecastTaskAndState:
    """TypedDicts — minimal shape sanity, not full validation (TypedDict doesn't validate)."""

    def test_forecast_task_minimal(self):
        task: ForecastTask = {"kind": "forecast.concept", "concept_name": "MCP"}
        assert task["kind"] == "forecast.concept"

    def test_forecast_state_can_carry_evidence(self):
        state: ForecastState = {
            "task": {"kind": "forecast.concept", "concept_name": "MCP"},
            "depth": 0,
            "evidence": {"total_mentions": 18},
        }
        assert state["depth"] == 0
        assert state["evidence"]["total_mentions"] == 18
