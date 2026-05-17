"""
Unit tests for ROMA node functions.

Atomizer, Aggregator, and routing functions are pure-Python over state
dicts — exercise them directly with crafted states. The Executor and
Planner involve I/O (Postgres, SLM); we test only the parts that don't
touch I/O.
"""

from __future__ import annotations

import pytest
from agentradar_supervisor.nodes import (
    MAX_DEPTH,
    _aggregate_concept,
    atomize,
    route_after_atomize,
    route_after_plan,
)

# ---- Atomizer -------------------------------------------------------------


class TestAtomize:
    def test_forecast_concept_is_atomic(self):
        result = atomize({"task": {"kind": "forecast.concept"}, "depth": 0})
        assert result["is_atomic"] is True

    def test_forecast_top_n_is_composite(self):
        result = atomize({"task": {"kind": "forecast.top_n"}, "depth": 0})
        assert result["is_atomic"] is False

    def test_forecast_digest_is_composite(self):
        result = atomize({"task": {"kind": "forecast.digest"}, "depth": 0})
        assert result["is_atomic"] is False

    def test_depth_cap_forces_atomic_even_for_composite_kind(self):
        result = atomize({"task": {"kind": "forecast.top_n"}, "depth": MAX_DEPTH})
        assert result["is_atomic"] is True

    def test_depth_exceeding_cap_forces_atomic(self):
        result = atomize({"task": {"kind": "forecast.digest"}, "depth": MAX_DEPTH + 5})
        assert result["is_atomic"] is True

    def test_depth_default_treats_missing_depth_as_zero(self):
        """If depth isn't set, it should be treated as 0 (top-level invocation)."""
        result = atomize({"task": {"kind": "forecast.concept"}})
        assert result["is_atomic"] is True


# ---- Routing functions ---------------------------------------------------


class TestRouting:
    def test_atomic_routes_to_execute(self):
        assert route_after_atomize({"is_atomic": True}) == "execute"

    def test_composite_routes_to_plan(self):
        assert route_after_atomize({"is_atomic": False}) == "plan"

    def test_missing_is_atomic_defaults_to_plan(self):
        """Defensive default: if Atomizer didn't write the field, treat as composite."""
        assert route_after_atomize({}) == "plan"

    def test_empty_subtasks_routes_to_aggregate(self):
        assert route_after_plan({"subtasks": []}) == "aggregate"

    def test_missing_subtasks_routes_to_aggregate(self):
        assert route_after_plan({}) == "aggregate"

    def test_nonempty_subtasks_routes_to_execute(self):
        assert route_after_plan({"subtasks": [{"kind": "forecast.concept"}]}) == "execute"


# ---- Aggregator ----------------------------------------------------------


class TestAggregate:
    """Confidence banding and weak-fallback behavior. Targets
    _aggregate_concept directly (the sync sub-aggregator) rather than
    the async top-level dispatch."""

    @pytest.mark.parametrize(
        "confidence,expected_band",
        [
            (0.0, "weak"),
            (0.39, "weak"),
            (0.4, "medium"),
            (0.69, "medium"),
            (0.7, "high"),
            (1.0, "high"),
        ],
    )
    def test_confidence_band_thresholds(self, confidence, expected_band):
        state = {
            "task": {"kind": "forecast.concept", "concept_name": "X"},
            "candidate_forecast": {
                "prediction": "p",
                "confidence": confidence,
                "reasoning": "r",
                "cited_concept_ids": [],
            },
            "evidence": {},
        }
        result = _aggregate_concept(state)
        assert result["confidence_band"] == expected_band
        assert result["final_forecast"]["confidence"] == confidence

    def test_missing_candidate_produces_weak_fallback(self):
        state = {
            "task": {"kind": "forecast.concept", "concept_name": "X"},
            "evidence": {"total_mentions": 5},
        }
        result = _aggregate_concept(state)
        assert result["confidence_band"] == "weak"
        ff = result["final_forecast"]
        assert ff["confidence"] == 0.0
        assert "Insufficient signal" in ff["prediction"]
        assert ff["evidence_snapshot"]["total_mentions"] == 5

    def test_aggregator_includes_concept_name_from_task(self):
        state = {
            "task": {"kind": "forecast.concept", "concept_name": "TargetConcept"},
            "candidate_forecast": {
                "prediction": "p",
                "confidence": 0.5,
                "reasoning": "r",
                "cited_concept_ids": ["A", "B"],
            },
            "evidence": {"x": 1},
        }
        result = _aggregate_concept(state)
        ff = result["final_forecast"]
        assert ff["concept_name"] == "TargetConcept"
        assert ff["cited_concept_ids"] == ["A", "B"]
        assert ff["evidence_snapshot"] == {"x": 1}

    def test_aggregator_preserves_reasoning(self):
        state = {
            "task": {"kind": "forecast.concept", "concept_name": "X"},
            "candidate_forecast": {
                "prediction": "p",
                "confidence": 0.5,
                "reasoning": "Multi-source convergence detected",
                "cited_concept_ids": [],
            },
            "evidence": {},
        }
        result = _aggregate_concept(state)
        assert result["final_forecast"]["reasoning"] == "Multi-source convergence detected"

    def test_aggregator_handles_empty_candidate_dict(self):
        state = {
            "task": {"kind": "forecast.concept", "concept_name": "X"},
            "candidate_forecast": {},
            "evidence": {},
        }
        result = _aggregate_concept(state)
        assert result["confidence_band"] == "weak"
        assert result["final_forecast"]["confidence"] == 0.0
