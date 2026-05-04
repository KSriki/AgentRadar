"""arXiv, GitHub, lab-blog, and other scouts that pull from public sources."""

from agentradar_supervisor.agents.scout.arxiv import ArxivScout

from agentradar_supervisor.agents.scout.tavily import TavilyScout

__all__ = ["ArxivScout", "TavilyScout"]