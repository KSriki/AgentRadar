"""AgentRadar specialist agents."""

from agentradar_supervisor.agents.base import Agent
from agentradar_supervisor.agents.critic import Critic
from agentradar_supervisor.agents.scout import ArxivScout, TavilyScout, TrendScout
from agentradar_supervisor.agents.forecaster import Forecaster

__all__ = ["Agent", "ArxivScout", "Critic", "TavilyScout", "TrendScout", "Forecaster"]