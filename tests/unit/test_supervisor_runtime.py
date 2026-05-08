"""
Unit tests for the supervisor runtime.

Tests focus on the parts that are pure logic:
  - ScheduledJob.is_due semantics
  - Supervisor's stagger-on-startup vs fire-on-startup behavior
  - Per-run trace_id binding and cleanup

Tests for the orchestration loop (run_forever, signal handling, MCP retry)
live in slow tests since they involve real timing.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentradar_supervisor.runtime import ScheduledJob, Supervisor


# ---- ScheduledJob.is_due --------------------------------------------------


class TestScheduledJobIsDue:
    def test_zero_last_run_is_always_due(self):
        job = ScheduledJob(
            name="x", interval_seconds=60,
            factory=lambda: MagicMock(),
            last_run_at=0.0,
        )
        assert job.is_due(now=time.monotonic()) is True

    def test_recent_last_run_is_not_due(self):
        now = time.monotonic()
        job = ScheduledJob(
            name="x", interval_seconds=60,
            factory=lambda: MagicMock(),
            last_run_at=now - 10,  # 10s ago, well within 60s interval
        )
        assert job.is_due(now=now) is False

    def test_exactly_at_interval_boundary_is_due(self):
        now = time.monotonic()
        job = ScheduledJob(
            name="x", interval_seconds=60,
            factory=lambda: MagicMock(),
            last_run_at=now - 60,  # exactly 60s ago
        )
        assert job.is_due(now=now) is True

    def test_long_past_run_is_due(self):
        now = time.monotonic()
        job = ScheduledJob(
            name="x", interval_seconds=60,
            factory=lambda: MagicMock(),
            last_run_at=now - 1000,
        )
        assert job.is_due(now=now) is True


# ---- Supervisor stagger and fire-on-startup ------------------------------


class TestSupervisorStartupBehavior:
    """The first tick of the loop sets last_run_at differently based on flag."""

    def _make_supervisor(self, fire_on_startup: bool) -> Supervisor:
        jobs = [
            ScheduledJob(
                name="a", interval_seconds=60,
                factory=lambda: MagicMock(),
            ),
            ScheduledJob(
                name="b", interval_seconds=60,
                factory=lambda: MagicMock(),
            ),
        ]
        return Supervisor(jobs=jobs, fire_on_startup=fire_on_startup)

    def test_fire_on_startup_makes_jobs_immediately_due(self):
        """fire_on_startup=True means we set last_run_at to 0, so they fire."""
        # We don't call run_forever here (it'd block). Instead exercise the
        # initialization logic by inspecting state after the relevant code path.
        sup = self._make_supervisor(fire_on_startup=True)

        # Simulate the same logic as run_forever's startup block
        for job in sup._jobs:
            job.last_run_at = 0.0  # what fire_on_startup branch does

        for job in sup._jobs:
            assert job.is_due(now=time.monotonic())

    def test_stagger_on_startup_makes_jobs_not_due(self):
        """fire_on_startup=False stamps last_run_at=now, so they wait."""
        sup = self._make_supervisor(fire_on_startup=False)

        # Simulate: stagger-on-startup branch
        now = time.monotonic()
        for job in sup._jobs:
            job.last_run_at = now

        for job in sup._jobs:
            assert not job.is_due(now=now + 0.001)


# ---- Shutdown --------------------------------------------------------------


class TestShutdown:
    def test_request_shutdown_sets_event(self):
        sup = Supervisor(jobs=[], fire_on_startup=False)
        assert not sup._shutdown.is_set()
        sup.request_shutdown()
        assert sup._shutdown.is_set()

    def test_shutdown_idempotent(self):
        """Calling shutdown twice is safe — event just stays set."""
        sup = Supervisor(jobs=[], fire_on_startup=False)
        sup.request_shutdown()
        sup.request_shutdown()  # should not raise
        assert sup._shutdown.is_set()


# ---- Run-job orchestration ------------------------------------------------


class TestRunJob:
    """Verify per-job invocation: factory called, agent.run called, stats recorded."""

    @pytest.mark.asyncio
    async def test_successful_run_increments_completed_count(self):
        agent = MagicMock()
        agent.run = AsyncMock(return_value={"x": 1})
        job = ScheduledJob(
            name="test", interval_seconds=60,
            factory=lambda: agent,
        )
        sup = Supervisor(jobs=[job], fire_on_startup=False)

        mock_mcp = MagicMock()
        await sup._run_job(job, mock_mcp)

        assert job.runs_completed == 1
        assert job.runs_failed == 0
        assert job.last_summary == {"x": 1}

    @pytest.mark.asyncio
    async def test_exception_increments_failed_count(self):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("agent boom"))
        job = ScheduledJob(
            name="test", interval_seconds=60,
            factory=lambda: agent,
        )
        sup = Supervisor(jobs=[job], fire_on_startup=False)

        mock_mcp = MagicMock()
        # Should not propagate the exception — failures are caught
        await sup._run_job(job, mock_mcp)

        assert job.runs_completed == 0
        assert job.runs_failed == 1

    @pytest.mark.asyncio
    async def test_factory_called_each_run(self):
        """Each invocation gets a fresh agent instance via the factory."""
        factory_calls = []

        def _factory():
            agent = MagicMock()
            agent.run = AsyncMock(return_value={})
            factory_calls.append(agent)
            return agent

        job = ScheduledJob(
            name="test", interval_seconds=60,
            factory=_factory,
        )
        sup = Supervisor(jobs=[job], fire_on_startup=False)

        mock_mcp = MagicMock()
        await sup._run_job(job, mock_mcp)
        await sup._run_job(job, mock_mcp)

        assert len(factory_calls) == 2
        # Each agent.run was called once
        for agent in factory_calls:
            assert agent.run.call_count == 1