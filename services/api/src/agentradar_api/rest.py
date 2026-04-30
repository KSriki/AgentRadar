"""
REST endpoints for the dashboard.

These are read-only views over the same data the MCP tools touch — agents
write via MCP, humans read via REST. Same store clients, same trace_id
propagation, same async patterns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from agentradar_core import get_logger
from agentradar_store import (
    get_neo4j_client,
    get_pg_client,
    get_slm_client,
    get_s3_client,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])


# ---------------------------------------------------------------------------
# /api/health — full system health (extends /health to include SLM)
# ---------------------------------------------------------------------------


@router.get("/health")
async def detailed_health() -> dict[str, Any]:
    """Healthcheck across all backing services, including the SLM."""
    n = get_neo4j_client()
    p = get_pg_client()
    s = get_s3_client()

    # SLM check — try a trivial generation. Bounded with try/except so a
    # broken SLM provider doesn't take the dashboard offline.
    slm_ok = False
    try:
        slm = get_slm_client()
        out = await slm.generate(
            system="Reply with exactly: OK",
            user="ping",
            max_tokens=8,
        )
        slm_ok = "OK" in out.upper()
    except Exception as exc:
        log.warning("rest.slm_healthcheck_failed", error=str(exc))

    return {
        "neo4j": await n.healthcheck(),
        "postgres": await p.healthcheck(),
        "s3": await s.healthcheck(),
        "slm": slm_ok,
    }


# ---------------------------------------------------------------------------
# /api/stats — counts that drive the overview widget
# ---------------------------------------------------------------------------


@router.get("/stats")
async def stats() -> dict[str, int]:
    """High-level system counts."""
    n = get_neo4j_client()
    p = get_pg_client()

    # Neo4j: concept + source counts
    async with n.session() as s:
        concepts = await (await s.run(
            "MATCH (c:Concept) RETURN count(c) AS n"
        )).single()
        sources = await (await s.run(
            "MATCH (s:Source) RETURN count(s) AS n"
        )).single()
        rels = await (await s.run(
            "MATCH ()-[r]->() RETURN count(r) AS n"
        )).single()

    # Postgres: pending queue counts by status
    pool = await p._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*)::int AS n "
            "FROM pending_triples GROUP BY status"
        )
    by_status = {r["status"]: r["n"] for r in rows}

    return {
        "concepts": concepts["n"],
        "sources": sources["n"],
        "relationships": rels["n"],
        "pending": by_status.get("pending", 0),
        "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0),
    }


# ---------------------------------------------------------------------------
# /api/pending — recent pending triples (Critic's queue, read-only view)
# ---------------------------------------------------------------------------


@router.get("/pending")
async def recent_pending(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent pending triples, oldest-first (FIFO)."""
    limit = max(1, min(limit, 100))
    p = get_pg_client()
    pending = await p.list_pending_triples(limit=limit)
    return [t.model_dump(mode="json") for t in pending]


# ---------------------------------------------------------------------------
# /api/recent-activity — last N decisions (approved or rejected)
# ---------------------------------------------------------------------------


@router.get("/recent-activity")
async def recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent Critic decisions, newest first."""
    limit = max(1, min(limit, 100))
    p = get_pg_client()
    pool = await p._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, proposer_agent, subject, predicate, object,
                   source_id, status, decided_at
            FROM pending_triples
            WHERE decided_at IS NOT NULL
            ORDER BY decided_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [
        {
            "id": str(r["id"]),
            "proposer_agent": r["proposer_agent"],
            "subject": r["subject"],
            "predicate": r["predicate"],
            "object": r["object"],
            "source_id": r["source_id"],
            "status": r["status"],
            "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# /api/top-concepts — top mentioned concepts in a window
# ---------------------------------------------------------------------------


@router.get("/top-concepts")
async def top_concepts(
    limit: int = 10, window_days: int = 90
) -> list[dict[str, Any]]:
    """
    Top concepts by mention count over the last N days. Velocity is
    computed via the same simple-slope heuristic the Forecaster uses,
    so this view previews what the Forecaster will see.
    """
    limit = max(1, min(limit, 50))
    window_days = max(1, min(window_days, 365))

    p = get_pg_client()
    pool = await p._ensure()
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT concept_name, COUNT(*)::int AS mentions
            FROM mention_events
            WHERE observed_at >= $1
            GROUP BY concept_name
            ORDER BY mentions DESC
            LIMIT $2
            """,
            cutoff, limit,
        )

    # Compute velocity per concept via the existing helper
    out = []
    for r in rows:
        v = await p.mention_velocity(r["concept_name"], window_days=window_days)
        out.append({
            "concept": r["concept_name"],
            "mentions": r["mentions"],
            "velocity": round(v["velocity"], 3),
            "buckets": v["buckets"],   # weekly counts for sparkline
        })
    return out


# ---------------------------------------------------------------------------
# /api/concepts/{name} — single-concept detail (used for click-throughs)
# ---------------------------------------------------------------------------


@router.get("/concepts/{name}")
async def concept_detail(name: str) -> dict[str, Any]:
    """Concept node + first-degree edges (mirrors the MCP get_concept tool)."""
    n = get_neo4j_client()
    raw = await n.fetch_concept(name)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Concept {name!r} not found")

    # Reuse the same Neo4j-temporal serialization the MCP tool uses
    from agentradar_api.mcp_tools import _serialize_neo4j

    return {
        "concept": _serialize_neo4j(raw["concept"]),
        "edges": [
            {
                "type": edge["type"],
                "props": _serialize_neo4j(edge["props"]),
                "other": _serialize_neo4j(edge["other"]),
            }
            for edge in raw["edges"]
            if edge.get("type") is not None
        ],
    }