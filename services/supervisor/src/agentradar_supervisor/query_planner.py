"""
Generate Tavily search queries from graph state.

Three strategies, each templated from data the agents have already gathered:

1. Corroboration — for singleton concepts (mentioned exactly once),
   search for a second independent source.
2. Velocity spikes — for concepts whose mention rate just jumped,
   search for the triggering announcement.
3. Adjacency — for high-output authorities, search for what else
   they've been working on.

This module is intentionally pure templating. No SLM calls; no network
I/O of its own. Ingests structured data from the store layer, emits
strings. That keeps it fast, deterministic, and trivial to test.

If template-generated queries underperform later, this is the natural
place to add an SLM-based query rewriter — same input, smarter output.
"""

from __future__ import annotations

from typing import Any

from agentradar_core import get_logger
from agentradar_store import get_neo4j_client, get_pg_client

log = get_logger(__name__)


# How many of each type to generate per planning cycle. Total derived
# queries per cycle is the sum of these — kept small so static queries
# from the YAML still get fair share of the round-robin.
DEFAULT_CORROBORATION_QUERIES = 3
DEFAULT_SPIKE_QUERIES = 2
DEFAULT_ADJACENCY_QUERIES = 2


async def generate_corroboration_queries(limit: int) -> list[str]:
    """For singleton concepts, generate 'find a second source' queries."""
    pg = get_pg_client()
    singletons = await pg.find_singleton_concepts(window_days=30, limit=limit)
    queries = [
        f"{item['concept']} agent framework OR protocol OR tool"
        for item in singletons
    ]
    log.info(
        "query_planner.corroboration",
        count=len(queries), concepts=[s["concept"] for s in singletons],
    )
    return queries


async def generate_spike_queries(limit: int) -> list[str]:
    """For velocity-spiked concepts, find the announcement/launch."""
    pg = get_pg_client()
    spikes = await pg.find_velocity_spikes(
        window_days=14, min_recent_mentions=3, limit=limit,
    )
    queries = [
        f"{item['concept']} announcement launch release"
        for item in spikes
    ]
    log.info(
        "query_planner.spikes",
        count=len(queries),
        spikes=[(s["concept"], s["recent_count"], s["prior_count"]) for s in spikes],
    )
    return queries


async def generate_adjacency_queries(limit: int) -> list[str]:
    """For top authorities, find what else they're working on."""
    n = get_neo4j_client()
    authorities = await n.list_top_authorities(limit=limit)
    queries = [
        f"new agent framework OR tool OR protocol from {item['authority']}"
        for item in authorities
    ]
    log.info(
        "query_planner.adjacency",
        count=len(queries),
        authorities=[a["authority"] for a in authorities],
    )
    return queries


async def derive_tavily_queries() -> list[str]:
    """
    Generate the full set of graph-derived queries for one Scout cycle.
    Returns a deduplicated list. Order doesn't matter — the supervisor's
    round-robin handles selection.

    Failures in any one strategy are caught and logged: a stale Postgres
    query shouldn't take down the whole derivation pipeline.
    """
    queries: list[str] = []

    for name, fn, limit in [
        ("corroboration", generate_corroboration_queries, DEFAULT_CORROBORATION_QUERIES),
        ("spikes", generate_spike_queries, DEFAULT_SPIKE_QUERIES),
        ("adjacency", generate_adjacency_queries, DEFAULT_ADJACENCY_QUERIES),
    ]:
        try:
            queries.extend(await fn(limit))
        except Exception as exc:
            log.warning(
                "query_planner.strategy_failed",
                strategy=name, error=str(exc),
            )

    # Dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            deduped.append(q)

    log.info("query_planner.derived_queries", total=len(deduped))
    return deduped