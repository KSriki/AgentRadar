"""
Unit tests for Session 2 state extensions.

Pins the DigestSynthesis schema (the SLM's synthesis contract) and
verifies the ForecastTask discriminator covers all three task kinds.
"""

from __future__ import annotations

import pytest
from agentradar_supervisor.state import (
    DigestSynthesis,
    ForecastState,
    ForecastTask,
)
from pydantic import ValidationError

# ---- DigestSynthesis validation -----------------------------------------


class TestDigestSynthesis:
    """The Pydantic model the digest's synthesis SLM call populates."""

    def test_valid_complete_payload(self):
        d = DigestSynthesis(
            themes="Convergence across MCP and A2A suggests protocol consolidation.",
            standout="A2A is the most notable for its multi-lab adoption velocity.",
        )
        assert "MCP" in d.themes
        assert d.standout.startswith("A2A")

    def test_missing_themes_rejected(self):
        with pytest.raises(ValidationError):
            DigestSynthesis(standout="x")

    def test_missing_standout_rejected(self):
        with pytest.raises(ValidationError):
            DigestSynthesis(themes="x")

    def test_empty_strings_accepted(self):
        """Empty strings are technically valid; aggregator's fallback handles them."""
        d = DigestSynthesis(themes="", standout="")
        assert d.themes == ""
        assert d.standout == ""

    def test_model_dump_round_trips(self):
        d = DigestSynthesis(themes="t", standout="s")
        restored = DigestSynthesis(**d.model_dump())
        assert restored.themes == "t"
        assert restored.standout == "s"


# ---- ForecastTask shape for all three task kinds -----------------------


class TestForecastTaskKinds:
    """TypedDicts don't validate at runtime, but we can sanity-check shape."""

    def test_concept_task_shape(self):
        task: ForecastTask = {
            "kind": "forecast.concept",
            "concept_name": "MCP",
        }
        assert task["kind"] == "forecast.concept"
        assert task["concept_name"] == "MCP"

    def test_topn_task_shape(self):
        task: ForecastTask = {
            "kind": "forecast.top_n",
            "top_n": 5,
        }
        assert task["kind"] == "forecast.top_n"
        assert task["top_n"] == 5

    def test_digest_task_shape(self):
        task: ForecastTask = {
            "kind": "forecast.digest",
            "top_n": 5,
            "digest_label": "Weekly digest 2026-05-17",
        }
        assert task["kind"] == "forecast.digest"
        assert task["digest_label"].startswith("Weekly")


# ---- ForecastState carries the composite fields ------------------------


class TestForecastStateSession2:
    """Session 2 added subtask_results, final_topn, final_digest to the state."""

    def test_state_can_carry_subtask_results(self):
        state: ForecastState = {
            "task": {"kind": "forecast.top_n", "top_n": 3},
            "depth": 0,
            "subtask_results": [
                {"forecast": {"concept_name": "MCP"}, "band": "high"},
                {"forecast": {"concept_name": "A2A"}, "band": "medium"},
            ],
        }
        assert len(state["subtask_results"]) == 2

    def test_state_can_carry_final_topn(self):
        state: ForecastState = {
            "task": {"kind": "forecast.top_n", "top_n": 3},
            "final_topn": [{"concept_name": "MCP"}, {"concept_name": "A2A"}],
        }
        assert len(state["final_topn"]) == 2

    def test_state_can_carry_final_digest(self):
        state: ForecastState = {
            "task": {"kind": "forecast.digest", "top_n": 5},
            "final_digest": {
                "label": "weekly",
                "themes": "themes go here",
                "standout": "standout pick",
                "forecasts": [],
                "average_confidence": 0.6,
            },
        }
        assert state["final_digest"]["label"] == "weekly"
        assert state["final_digest"]["average_confidence"] == 0.6
