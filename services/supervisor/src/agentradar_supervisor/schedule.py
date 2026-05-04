"""
Schedule configuration — env-driven so deployments override cadence
without code changes.

Each agent has an interval (seconds between consecutive runs). The
supervisor checks every TICK_SECONDS whether any agent is due.

Env var conventions:
    SCHEDULE_<AGENT>_INTERVAL          interval in seconds
    SCHEDULE_<AGENT>_<EXTRA>           agent-specific config

Defaults are the "moderate cadence" we agreed on:
    Scout every 2h, Critic every 15m
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScheduleSettings(BaseSettings):
    """Intervals and per-agent config. Override via env."""

    model_config = SettingsConfigDict(env_prefix="SCHEDULE_", extra="ignore")

    # Scout: 2 hours by default. Cycles through configured arXiv categories.
    scout_arxiv_interval: int = Field(default=2 * 60 * 60)
    scout_arxiv_categories: str = "cs.AI,cs.LG,cs.CL"
    scout_arxiv_max_papers: int = 50

    # Critic: 15 minutes — drains the queue 8x faster than Scout fills it,
    # keeping steady-state queue depth small.
    critic_interval: int = Field(default=15 * 60)
    critic_batch_limit: int = 50

    # Demo escape hatch: when true, every agent fires immediately at startup
    # rather than waiting for its first interval. Useful for screen recording.
    fire_on_startup: bool = False

    # Tavily Scout: 6h cadence — slower than arXiv because Tavily costs
    # credits and the open web doesn't change as fast as arXiv submissions.
    scout_tavily_interval: int = Field(default=6 * 60 * 60)
    scout_tavily_queries: str = (
        "new AI agent protocols announced"
        ",agent-to-agent communication standards"
        ",agentic AI architectural patterns"
        ",MCP Model Context Protocol updates"
        ",LangGraph multi-agent orchestration"
        ",AI agent framework benchmarks"
    )
    scout_tavily_max_results: int = 8


def load_schedule() -> ScheduleSettings:
    """Read the schedule from env. Cached implicitly via pydantic."""
    return ScheduleSettings()