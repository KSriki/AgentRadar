"""
Common types for trend sources.

A TrendItem is the converged shape — every source adapter emits these
regardless of origin. Downstream code (storage + extraction + proposal)
operates on TrendItems and never has to know which source produced one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class TrendItem:
    """One trend signal from any source. Self-describing."""

    source_kind: str  # "github" | "hn" | "lab_rss" — discriminator
    url: str  # the canonical URL of the trend item
    title: str  # display title
    summary: str  # description, README excerpt, story title — the
    # piece of text the SLM will extract concepts from
    published_at: datetime
    extra: dict[str, Any] = field(default_factory=dict)
    # ^ source-specific fields (stars, points, author) that we want to
    # keep in S3 for later inspection but don't necessarily promote
    # to the graph.

    @property
    def source_id(self) -> str:
        """Stable Source.id; same hashing approach as Tavily for consistency."""
        url_hash = hashlib.sha256(self.url.encode()).hexdigest()[:32]
        return f"trend-{self.source_kind}:{url_hash}"

    @property
    def s3_key(self) -> str:
        return f"trends/{self.source_kind}/{self.source_id.split(':')[-1]}.json"


class TrendSource(Protocol):
    """Each trend-source adapter implements this interface."""

    name: str  # short identifier, e.g. "github", "hn", "lab_rss"

    async def fetch(self) -> list[TrendItem]:
        """Pull the latest items from this source. May return [] gracefully."""
        ...
