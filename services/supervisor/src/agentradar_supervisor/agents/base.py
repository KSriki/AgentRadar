"""
Agent protocol — what every specialist must implement to be schedulable.

Agents are constructed once at startup and reused across many runs. They
hold no per-run state in instance variables; instead they receive their
inputs through the run() method's parameters and return structured output.

Why a class with a run() method instead of just a function:
- Agents may have setup state (compiled regex, prompts, configs) that's
  expensive to construct but constant per-run. Class init is the place.
- Agents that need internal helper methods can have them without polluting
  the module namespace.
- The Protocol gives us static typing for the runtime's registry.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fastmcp import Client


@runtime_checkable
class Agent(Protocol):
    """Every specialist agent implements this interface."""

    name: str  # short identifier, e.g. "scout-arxiv"

    async def run(self, mcp: Client) -> dict[str, Any]:
        """
        Execute one full agent invocation.

        Args:
            mcp: A connected fastmcp Client. The runtime owns its lifecycle;
                 agents never construct or close it.

        Returns:
            Structured summary of what happened (counts, IDs, timings).
            Used by the runtime for logging and observability — not for
            decision-making about whether to run again (that's the
            scheduler's concern).
        """
        ...