"""
Unit tests for ROMA composite-workflow node implementations.

Atomizer / Planner / Aggregator behavior over composite task kinds.
Parent-context distillation contract. Confidence normalization helper.

Also: a meta-test that asserts no agent module imports get_pg_client
directly — this codifies the architectural rule that emerged from the
Session 1 event-loop debugging.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentradar_supervisor import nodes
from agentradar_supervisor.agents import critic, forecaster, scout
from agentradar_supervisor.nodes import (
    MAX_DEPTH,
    _aggregate_concept,
    _aggregate_topn,
    _distill_parent_context,
    aggregate,
    atomize,
    plan,
    route_after_atomize,
    route_after_plan,
)

# ---- Atomizer for composite kinds ---------------------------------------


class TestAtomizeComposite:
    """The Session 1 atomic-kind tests pinned forecast.concept. These
    cover the composite kinds added in Session 2."""

    @pytest.mark.parametrize("kind", ["forecast.top_n", "forecast.digest"])
    def test_composite_kinds_are_non_atomic(self, kind):
        result = atomize({"task": {"kind": kind}, "depth": 0})
        assert result["is_atomic"] is False

    @pytest.mark.parametrize("kind", ["forecast.top_n", "forecast.digest"])
    def test_depth_cap_forces_atomic_for_composite(self, kind):
        """At MAX_DEPTH, even composite kinds get atomic dispatch as
        a safety against runaway recursion."""
        result = atomize({"task": {"kind": kind}, "depth": MAX_DEPTH})
        assert result["is_atomic"] is True

    def test_depth_above_cap_forces_atomic(self):
        result = atomize({
            "task": {"kind": "forecast.digest"},
            "depth": MAX_DEPTH + 5,
        })
        assert result["is_atomic"] is True


# ---- Planner ------------------------------------------------------------


class TestPlanner:
    """The Planner runs only for composite kinds. For each, verify that
    it produces the expected subtask shapes."""

    @pytest.mark.asyncio
    async def test_plan_topn_emits_n_concept_subtasks(self):
        mcp = MagicMock()
        mcp.call_tool = AsyncMock(return_value=MagicMock(data={
            "concept_names": ["MCP", "A2A", "ROMA"],
        }))
        state = {
            "task": {"kind": "forecast.top_n", "top_n": 3},
            "mcp": mcp,
        }
        result = await plan(state)
        subtasks = result["subtasks"]
        assert len(subtasks) == 3
        for st in subtasks:
            assert st["kind"] == "forecast.concept"
        names = [st["concept_name"] for st in subtasks]
        assert names == ["MCP", "A2A", "ROMA"]

    @pytest.mark.asyncio
    async def test_plan_topn_empty_when_no_candidates(self):
        """select_top_n_concepts returns empty → planner returns empty subtasks."""
        mcp = MagicMock()
        mcp.call_tool = AsyncMock(return_value=MagicMock(data={
            "concept_names": [],
        }))
        result = await plan({
            "task": {"kind": "forecast.top_n", "top_n": 5},
            "mcp": mcp,
        })
        assert result["subtasks"] == []

    @pytest.mark.asyncio
    async def test_plan_digest_emits_one_topn_subtask(self):
        result = await plan({
            "task": {"kind": "forecast.digest", "top_n": 5},
            "mcp": MagicMock(),  # not called for digest planning
        })
        subtasks = result["subtasks"]
        assert len(subtasks) == 1
        assert subtasks[0]["kind"] == "forecast.top_n"
        assert subtasks[0]["top_n"] == 5

    @pytest.mark.asyncio
    async def test_plan_unknown_kind_returns_empty(self):
        result = await plan({
            "task": {"kind": "forecast.unknown"},
            "mcp": MagicMock(),
        })
        assert result["subtasks"] == []

    @pytest.mark.asyncio
    async def test_plan_no_mcp_returns_empty(self):
        """Defensive: missing mcp in state should fail gracefully."""
        result = await plan({"task": {"kind": "forecast.top_n", "top_n": 5}})
        assert result["subtasks"] == []


# ---- Parent-context distillation ----------------------------------------


class TestParentContextDistillation:
    """The README's anti-context-bloat claim: recursive children receive
    a SMALL distilled context, not the full parent state. These tests
    pin the contract — what's in the dict, what's not."""

    def test_digest_parent_distills_to_audience_hint(self):
        state = {
            "task": {"kind": "forecast.digest", "top_n": 5},
            "depth": 0,
            "evidence": {"large_dict": "should not leak to child"},
            "candidate_forecast": {"also_should_not_leak": True},
        }
        ctx = _distill_parent_context(state)
        # Should contain audience hint
        assert ctx.get("ancestor_kind") == "digest"
        assert "audience" in ctx
        # Should NOT contain the parent's evidence or candidate
        assert "large_dict" not in ctx
        assert "also_should_not_leak" not in ctx

    def test_topn_parent_distills_to_total_count(self):
        state = {
            "task": {"kind": "forecast.top_n", "top_n": 7},
            "evidence": {"leak_me": "should not appear"},
        }
        ctx = _distill_parent_context(state)
        assert ctx.get("ancestor_kind") == "top_n"
        assert ctx.get("total_in_series") == 7
        assert "leak_me" not in ctx

    def test_concept_parent_returns_empty(self):
        """A concept is atomic; no children to distill to."""
        ctx = _distill_parent_context({
            "task": {"kind": "forecast.concept", "concept_name": "MCP"},
        })
        assert ctx == {}


# ---- Routing functions (unchanged from Session 1, regression check) ----


class TestRoutingFunctionsSession2:
    """Re-test the routing functions in a Session 2 context with composite
    subtask lists, to catch any regression where the additions broke
    Session 1 behavior."""

    def test_composite_routes_to_plan(self):
        assert route_after_atomize({"is_atomic": False}) == "plan"

    def test_atomic_routes_to_execute(self):
        assert route_after_atomize({"is_atomic": True}) == "execute"

    def test_subtasks_list_routes_to_execute(self):
        assert route_after_plan({
            "subtasks": [{"kind": "forecast.concept", "concept_name": "X"}],
        }) == "execute"

    def test_empty_subtasks_routes_to_aggregate(self):
        assert route_after_plan({"subtasks": []}) == "aggregate"


# ---- Aggregator paths ---------------------------------------------------


class TestAggregateTopn:
    """Aggregator path for forecast.top_n: collects forecast-containing
    subtask results, drops error/non-forecast entries."""

    def test_topn_collects_forecast_subtask_results(self):
        state = {
            "task": {"kind": "forecast.top_n", "top_n": 3},
            "subtask_results": [
                {"forecast": {"concept_name": "MCP", "confidence": 0.7}},
                {"forecast": {"concept_name": "A2A", "confidence": 0.5}},
                {"forecast": {"concept_name": "ROMA", "confidence": 0.6}},
            ],
        }
        result = _aggregate_topn(state)
        topn = result["final_topn"]
        assert len(topn) == 3
        assert [f["concept_name"] for f in topn] == ["MCP", "A2A", "ROMA"]

    def test_topn_drops_subtask_errors(self):
        """If a subtask errored, its result dict has {error: ...} not {forecast: ...}.
        The aggregator should drop those rather than crashing."""
        state = {
            "task": {"kind": "forecast.top_n", "top_n": 3},
            "subtask_results": [
                {"forecast": {"concept_name": "MCP"}},
                {"error": "SLM call timed out", "subtask": {"kind": "forecast.concept"}},
                {"forecast": {"concept_name": "A2A"}},
            ],
        }
        result = _aggregate_topn(state)
        assert len(result["final_topn"]) == 2
        assert [f["concept_name"] for f in result["final_topn"]] == ["MCP", "A2A"]

    def test_topn_empty_results_returns_empty_list(self):
        result = _aggregate_topn({
            "task": {"kind": "forecast.top_n", "top_n": 5},
            "subtask_results": [],
        })
        assert result["final_topn"] == []


class TestAggregateDigest:
    """Digest aggregator combines inner top_n result with an SLM-synth call.
    Most of these tests exercise the no-SLM-needed fallback paths."""

    @pytest.mark.asyncio
    async def test_digest_no_inner_topn_uses_fallback(self):
        """If the inner top_n subtask didn't produce a topn result, the
        digest should still complete with placeholder content."""
        state = {
            "task": {"kind": "forecast.digest", "top_n": 5},
            "subtask_results": [{"error": "topn failed"}],
        }
        result = await aggregate(state)
        digest = result["final_digest"]
        assert digest["forecasts"] == []
        assert "No forecasts" in digest["themes"]
        assert result["confidence_band"] == "weak"

    @pytest.mark.asyncio
    async def test_digest_empty_inner_topn_uses_fallback(self):
        """Inner topn ran but produced an empty list (e.g., everything in cooldown).
        Different from above — the inner subtask succeeded with []."""
        state = {
            "task": {"kind": "forecast.digest", "top_n": 5},
            "subtask_results": [{"topn": []}],
        }
        result = await aggregate(state)
        assert result["final_digest"]["forecasts"] == []
        assert "No qualifying concepts" in result["final_digest"]["themes"]
        assert result["confidence_band"] == "weak"


# ---- Architectural rule: agents don't touch storage clients directly ---
class TestArchitecturalRules:
    """Meta-tests codifying project conventions that emerged from the
    event-loop debugging. These are deliberately fragile — they break
    when the rule breaks, which is the entire point."""

    def test_forecaster_does_not_import_get_pg_client(self):
        """The Session 1 lesson: agent code must never reach past the MCP
        tool layer to hit Postgres directly. asyncpg has event-loop affinity
        that breaks under the supervisor's per-tick session pattern."""
        source = inspect.getsource(forecaster)
        assert "get_pg_client" not in source, (
            "Forecaster must not import get_pg_client. Use MCP tools for "
            "DB access. See architectural notes in RESUME_BULLETS.md."
        )

    def test_nodes_module_does_not_import_get_pg_client(self):
        """Same rule for the ROMA node functions."""
        source = inspect.getsource(nodes)
        assert "get_pg_client" not in source, (
            "ROMA nodes must not import get_pg_client. Use MCP tools."
        )

    def test_critic_agent_does_not_import_get_pg_client(self):
        source = inspect.getsource(critic)
        assert "get_pg_client" not in source

    def test_scout_agents_do_not_import_get_pg_client(self):
        source = inspect.getsource(scout)
        assert "get_pg_client" not in source

    def test_agents_can_still_import_slm_client(self):
        """get_slm_client is allowed — stateless HTTP client, no
        event-loop affinity. This test pins the distinction."""
        source = inspect.getsource(nodes)
        assert "get_slm_client" in source, (
            "Confirm SLM client usage is still permitted (httpx-based, "
            "no event-loop affinity)."
        )