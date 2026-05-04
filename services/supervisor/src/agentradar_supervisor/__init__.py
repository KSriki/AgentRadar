"""Long-running supervisor that schedules and runs AgentRadar's agents."""

from agentradar_supervisor.agents import Agent, Critic, ArxivScout
from agentradar_supervisor.runtime import Supervisor, build_supervisor, main

__all__ = [
    "Agent",
    "Critic",
    "ArxivScout",
    "Supervisor",
    "build_supervisor",
    "main",
]