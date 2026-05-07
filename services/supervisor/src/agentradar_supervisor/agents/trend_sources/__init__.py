"""
Trend source adapters. Each implements TrendSource Protocol and emits
TrendItem instances that downstream code (storage, SLM extraction,
proposal) treats uniformly regardless of origin.
"""

from agentradar_supervisor.agents.trend_sources.base import (
    TrendItem,
    TrendSource,
)
from agentradar_supervisor.agents.trend_sources.github import GithubTrendSource
from agentradar_supervisor.agents.trend_sources.hn import HnTrendSource
from agentradar_supervisor.agents.trend_sources.lab_rss import LabRssTrendSource

__all__ = [
    "TrendItem",
    "TrendSource",
    "GithubTrendSource",
    "HnTrendSource",
    "LabRssTrendSource",
]