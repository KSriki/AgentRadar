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
from agentradar_supervisor.agents import Agent, ArxivScout, Critic, TavilyScout
from agentradar_supervisor.schedule import ScheduleSettings, load_schedule

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

        async with self._mcp_session() as mcp:
            while not self._shutdown.is_set():
                await self._tick(mcp)
                # Wait for next tick OR shutdown — whichever first.
                # Cleanest way to make Ctrl-C snappy.
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=TICK_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass

        log.info("supervisor.loop_exited")

    async def _tick(self, mcp: Client) -> None:
        """Check every job; run the ones that are due."""
        now = time.monotonic()
        for job in self._jobs:
            if not job.is_due(now):
                continue
            await self._run_job(job, mcp)
            # Mark completion AFTER the run finishes (not before) so a slow
            # job can't overlap with itself on the next tick.
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
        One persistent MCP client for the whole supervisor lifetime.
        Retries connection on startup since the api container may not be
        ready yet even after its healthcheck flips green (TCP ready != MCP ready).
        """
        attempts = 0
        max_attempts = 30
        backoff = 2.0

        while True:
            try:
                async with Client(MCP_URL) as client:
                    # Sanity-check the connection by listing tools.
                    # If list_tools() works, every other tool call should too.
                    await client.list_tools()
                    log.info("supervisor.mcp_connected", url=MCP_URL)
                    yield client
                    return
            except Exception as exc:
                attempts += 1
                if attempts >= max_attempts:
                    log.error(
                        "supervisor.mcp_connect_failed_giving_up",
                        url=MCP_URL,
                        attempts=attempts,
                        error=str(exc),
                    )
                    raise
                log.warning(
                    "supervisor.mcp_connect_retry",
                    url=MCP_URL,
                    attempt=attempts,
                    error=str(exc),
                )
                await asyncio.sleep(backoff)


def build_supervisor() -> Supervisor:
    """Construct the supervisor with its scheduled jobs."""
    cfg: ScheduleSettings = load_schedule()

    # arXiv categories — round-robin
    arxiv_categories = [
        c.strip() for c in cfg.scout_arxiv_categories.split(",") if c.strip()
    ]
    if not arxiv_categories:
        raise ValueError("SCHEDULE_SCOUT_ARXIV_CATEGORIES must be non-empty")

    # Tavily queries — round-robin
    tavily_queries = [
        q.strip() for q in cfg.scout_tavily_queries.split(",") if q.strip()
    ]
    if not tavily_queries:
        raise ValueError("SCHEDULE_SCOUT_TAVILY_QUERIES must be non-empty")

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
            name="critic",
            interval_seconds=cfg.critic_interval,
            factory=make_critic,
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