"""
Supervisor runtime — long-running scheduler that fires agents at their intervals.

Architecture:
- One asyncio event loop, one process.
- Owns the MCP client lifecycle (connect once at startup, share across runs).
- Checks every TICK_SECONDS whether any agent is due.
- Runs due agents sequentially: the SLM is the bottleneck, parallelism just
  invites rate-limiting and makes logs harder to follow.
- Graceful shutdown on SIGINT/SIGTERM with bounded retry on MCP connect.
- Per-agent-run trace_id binding so structured logs can be filtered to one
  invocation across api, supervisor, and SLM logs.

Override schedule and target via env:
    SCHEDULE_*  — see schedule.py
    MCP_URL     — defaults to api container in compose; override for dev
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Callable

from fastmcp import Client

from agentradar_core import (
    bind_trace_id,
    clear_trace_context,
    configure_logging,
    get_logger,
)

from agentradar_supervisor.agents import (
    Agent, ArxivScout, Critic, Forecaster, TavilyScout, TrendScout,
)
from agentradar_supervisor.agents.trend_sources import (
    GithubTrendSource, HnTrendSource, LabRssTrendSource,
)
from agentradar_supervisor.schedule import ScheduleSettings, load_schedule
from agentradar_supervisor.config_loader import load_tavily_queries




configure_logging()
log = get_logger("supervisor")


# Granularity of "is anything due?" — 30s is a good tradeoff:
# agents fire on time without the supervisor spending more cycles
# checking than running them.
TICK_SECONDS = 30

MCP_URL = os.getenv("MCP_URL", "http://api:8000/mcp/")


@dataclass
class ScheduledJob:
    """One agent + its schedule + last-run state."""

    name: str
    interval_seconds: int
    factory: Callable[[], Agent]
    last_run_at: float = 0.0  # monotonic timestamp of last completion
    runs_completed: int = 0
    runs_failed: int = 0
    last_summary: dict[str, Any] = field(default_factory=dict)

    def is_due(self, now: float) -> bool:
        return (now - self.last_run_at) >= self.interval_seconds


class _DigestRunnerAgent:
    """Adapter so a digest workflow plugs into the supervisor's per-job
    runner just like any other Agent."""

    name = "digest"

    def __init__(self, top_n: int = 5) -> None:
        self._top_n = top_n

    async def run(self, mcp: Client) -> dict[str, Any]:
        forecaster = Forecaster()
        return await forecaster.run_digest(mcp, top_n=self._top_n)

class Supervisor:
    """The scheduler loop."""

    def __init__(self, jobs: list[ScheduledJob], fire_on_startup: bool) -> None:
        self._jobs = jobs
        self._fire_on_startup = fire_on_startup
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        log.info("supervisor.shutdown_requested")
        self._shutdown.set()

    async def run_forever(self) -> None:
        """Main entry. Connect MCP, then tick until shutdown."""
        if self._fire_on_startup:
            log.info("supervisor.fire_on_startup")
            for job in self._jobs:
                job.last_run_at = 0.0  # ancient history → due immediately
        else:
            # Stagger: pretend everyone just ran, so we wait one full
            # interval before the first real fire. Avoids stampeding the
            # SLM at startup.
            now = time.monotonic()
            for job in self._jobs:
                job.last_run_at = now

        log.info(
            "supervisor.loop_started",
            jobs=[
                {"name": j.name, "interval_s": j.interval_seconds}
                for j in self._jobs
            ],
            fire_on_startup=self._fire_on_startup,
            mcp_url=MCP_URL,
        )

       
        log.info(
            "supervisor.loop_started",
            jobs=[
                {"name": j.name, "interval_s": j.interval_seconds}
                for j in self._jobs
            ],
            fire_on_startup=self._fire_on_startup,
            mcp_url=MCP_URL,
        )
        while not self._shutdown.is_set():
            await self._tick()                        # ← no shared mcp
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(), timeout=TICK_SECONDS
                )
            except asyncio.TimeoutError:
                pass

        log.info("supervisor.loop_exited")

    async def _tick(self) -> None:
        """Check every job; run the ones that are due with a fresh MCP session."""
        now = time.monotonic()
        for job in self._jobs:
            if not job.is_due(now):
                continue
            try:
                async with self._mcp_session() as mcp:
                    await self._run_job(job, mcp)
            except Exception as exc:
                log.error(
                    "supervisor.tick_session_failure",
                    job=job.name, error=str(exc),
                )
            job.last_run_at = time.monotonic()

    async def _run_job(self, job: ScheduledJob, mcp: Client) -> None:
        """Run one agent invocation with trace_id binding and error capture."""
        # New trace_id per run — every log emitted by the agent in this
        # invocation gets it automatically via contextvars.
        trace_id = f"{job.name}-{int(time.time())}"
        bind_trace_id(trace_id)
        try:
            agent = job.factory()
            log.info(
                "supervisor.job.start",
                job=job.name,
                run_number=job.runs_completed + job.runs_failed + 1,
            )
            started_at = datetime.now(UTC)
            try:
                summary = await agent.run(mcp)
                duration_s = (datetime.now(UTC) - started_at).total_seconds()
                job.runs_completed += 1
                job.last_summary = summary
                log.info(
                    "supervisor.job.done",
                    job=job.name,
                    duration_s=round(duration_s, 2),
                    **summary,
                )
            except Exception as exc:
                duration_s = (datetime.now(UTC) - started_at).total_seconds()
                job.runs_failed += 1
                # log.exception captures stack trace; structured fields
                # supplement it with job name + duration for filtering.
                log.exception(
                    "supervisor.job.failed",
                    job=job.name,
                    duration_s=round(duration_s, 2),
                    error=str(exc),
                )
        finally:
            clear_trace_context()

    @asynccontextmanager
    async def _mcp_session(self) -> AsyncIterator[Client]:
        """
        Per-run MCP session. Each job gets a fresh session; session death
        affects only the current job, not the supervisor lifetime.

        Retries on initial connect with exponential backoff to handle
        the api-not-ready-yet startup case.
        """
        attempts = 0
        max_attempts = 5
        backoff = 1.0

        while True:
            try:
                async with Client(MCP_URL) as client:
                    await client.list_tools()  # sanity check
                    yield client
                    return
            except Exception as exc:
                attempts += 1
                if attempts >= max_attempts:
                    log.error(
                        "supervisor.mcp_session_failed",
                        url=MCP_URL, attempts=attempts, error=str(exc),
                    )
                    raise
                log.warning(
                    "supervisor.mcp_session_retry",
                    url=MCP_URL, attempt=attempts, error=str(exc),
                )
                await asyncio.sleep(backoff)
                backoff *= 2  # exponential


def build_supervisor() -> Supervisor:
    """Construct the supervisor with its scheduled jobs."""
    cfg: ScheduleSettings = load_schedule()

    # arXiv categories — round-robin
    arxiv_categories = [
        c.strip() for c in cfg.scout_arxiv_categories.split(",") if c.strip()
    ]
    if not arxiv_categories:
        raise ValueError("SCHEDULE_SCOUT_ARXIV_CATEGORIES must be non-empty")

    # Tavily queries from YAML config file (reloadable on supervisor restart
    # without rebuilding the container — just edit the file and restart).
    tavily_queries = asyncio.run(load_tavily_queries())

    arxiv_idx = 0
    tavily_idx = 0

    def make_arxiv_scout() -> Agent:
        nonlocal arxiv_idx
        category = arxiv_categories[arxiv_idx % len(arxiv_categories)]
        arxiv_idx += 1
        return ArxivScout(
            category=category,
            max_papers=cfg.scout_arxiv_max_papers,
        )

    def make_tavily_scout() -> Agent:
        nonlocal tavily_idx
        query = tavily_queries[tavily_idx % len(tavily_queries)]
        tavily_idx += 1
        return TavilyScout(
            query=query,
            max_results=cfg.scout_tavily_max_results,
        )
    
    def make_forecaster() -> Agent:
        # Pass concept_name=None so the Forecaster auto-selects the
        # highest-velocity-not-recently-forecasted concept each run.
        return Forecaster(concept_name=None)
    
    def make_digest_forecaster() -> Agent:
        # Same Forecaster class, but the runner will dispatch on a
        # composite workflow rather than the atomic default.
        return _DigestRunnerAgent(top_n=cfg.digest_top_n)
    
    trend_source_factories = [
        lambda: GithubTrendSource(),
        lambda: HnTrendSource(),
        lambda: LabRssTrendSource(),
    ]
    trend_idx = 0

    def make_trend_scout() -> Agent:
        nonlocal trend_idx
        source_factory = trend_source_factories[trend_idx % len(trend_source_factories)]
        trend_idx += 1
        return TrendScout(source=source_factory())

    def make_critic() -> Agent:
        return Critic(batch_limit=cfg.critic_batch_limit, dry_run=False)

    jobs = [
        ScheduledJob(
            name="scout-arxiv",
            interval_seconds=cfg.scout_arxiv_interval,
            factory=make_arxiv_scout,
        ),
        ScheduledJob(
            name="scout-tavily",
            interval_seconds=cfg.scout_tavily_interval,
            factory=make_tavily_scout,
        ),
        ScheduledJob(
            name="scout-trends",
            interval_seconds=cfg.scout_trends_interval,
            factory=make_trend_scout,
        ),
        ScheduledJob(
            name="critic",
            interval_seconds=cfg.critic_interval,
            factory=make_critic,
        ),
        ScheduledJob(
            name="forecaster",
            interval_seconds=cfg.forecaster_interval,
            factory=make_forecaster,
        ),
        ScheduledJob(
            name="digest-weekly",
            interval_seconds=cfg.digest_interval,
            factory=make_digest_forecaster,
        ),
    ]
    return Supervisor(jobs=jobs, fire_on_startup=cfg.fire_on_startup)


def main() -> None:
    """Process entry point. Wires up signals and runs the loop."""
    supervisor = build_supervisor()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler(signum: int) -> None:
        log.info("supervisor.signal_received", signum=signum)
        supervisor.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler, sig)

    try:
        loop.run_until_complete(supervisor.run_forever())
    finally:
        loop.close()


if __name__ == "__main__":
    main()