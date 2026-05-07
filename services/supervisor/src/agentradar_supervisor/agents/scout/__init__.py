"""Scout agents that pull from various public sources."""

from agentradar_supervisor.agents.scout.arxiv import ArxivScout
from agentradar_supervisor.agents.scout.tavily import TavilyScout
from agentradar_supervisor.agents.scout.trends import TrendScout

__all__ = ["ArxivScout", "TavilyScout", "TrendScout"]