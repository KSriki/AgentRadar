"""
MCP tool definitions for the AgentRadar API service.

Exposes the knowledge store as MCP tools, mounted on the parent FastAPI app
at /mcp. Tool semantics are identical to a standalone MCP server; only the
transport (HTTP via FastAPI) differs.

Design contract:
- Tools are thin wrappers over agentradar_store clients
- The proposer-critic gate is enforced HERE — propose_triple writes only to
  the pending queue; approve_triple is the only path that commits to Neo4j
- Lazy connections — first tool call triggers the singleton clients to connect
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from agentradar_core import (
    SourceType,
    Triple,
    TripleStatus,
    get_logger,
)
from agentradar_store import (
    get_embedding_client,
    get_neo4j_client,
    get_pg_client,
    get_s3_client,
)
from fastmcp import FastMCP

log = get_logger(__name__)

mcp = FastMCP("agentradar")

# HELPER


def _serialize_neo4j(value: Any) -> Any:
    """
    Recursively convert Neo4j driver types into JSON-serializable Python types.
    Neo4j returns its own DateTime/Date/Time/Duration classes which fastmcp
    cannot serialize automatically; cast them to ISO strings.
    """
    # Neo4j temporal types all expose .iso_format()
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if isinstance(value, dict):
        return {k: _serialize_neo4j(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_neo4j(v) for v in value]
    return value


# Cypher-identifier validation (defense in depth; Critic also validates)
_CYPHER_IDENT = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


# ---------------------------------------------------------------------------
# Concept lookup / search
# ---------------------------------------------------------------------------


@mcp.tool
async def search_concepts(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Vector-similarity search over concept embeddings. Use this first when
    deciding whether a candidate concept is novel or maps to an existing node.

    Args:
        query: Free-text description (e.g., "model context protocol",
               "agent-to-agent communication standard").
        limit: Max results, capped at 50.

    Returns:
        List of {concept_name, description, similarity}, descending by similarity.
    """
    limit = max(1, min(limit, 50))
    emb = get_embedding_client()
    pg = get_pg_client()
    embedding = await emb.embed_one(query)
    return await pg.search_similar_concepts(embedding, limit=limit)


@mcp.tool
async def get_concept(name: str) -> dict[str, Any]:
    """
    Fetch a Concept node by exact name with its first-degree relationships.
    Returns {"found": false, "concept": null, "edges": []} if no concept exists.
    """
    n = get_neo4j_client()
    raw = await n.fetch_concept(name)

    if raw is None:
        return {"found": False, "concept": None, "edges": []}

    return {
        "found": True,
        "concept": _serialize_neo4j(raw["concept"]),
        "edges": [
            {
                "type": edge["type"],
                "props": _serialize_neo4j(edge["props"]),
                "other": _serialize_neo4j(edge["other"]),
            }
            for edge in raw["edges"]
            if edge.get("type") is not None  # filters out OPTIONAL MATCH nulls
        ],
    }


# ---------------------------------------------------------------------------
# Triple proposal — writes to pending queue, NEVER directly to graph
# ---------------------------------------------------------------------------


@mcp.tool
async def propose_triple(
    proposer_agent: str,
    subject: str,
    predicate: str,
    object: str,
    source_id: str,
    confidence: float,
) -> dict[str, Any]:
    """
    Propose a (subject, predicate, object) triple. The triple is NOT committed
    to the graph; it goes to a pending queue for the Critic to validate.

    Args:
        proposer_agent: Caller identifier (e.g., "scout-arxiv", "extractor").
        subject: Subject concept name.
        predicate: Relationship type — must match [A-Z][A-Z0-9_]{0,63}.
        object: Object concept name.
        source_id: Stable identifier of the source supporting this claim.
        confidence: Proposer's self-reported confidence, in [0.0, 1.0].

    Returns:
        {triple_id, status} where status is "pending" on first proposal.
        Re-proposing the same (subject, predicate, object, source_id) is
        idempotent and may update the stored confidence upward.
    """
    if not _CYPHER_IDENT.match(predicate):
        raise ValueError(f"Invalid predicate {predicate!r}: must match [A-Z][A-Z0-9_]{{0,63}}")
    triple = Triple(
        subject=subject,
        predicate=predicate,
        object=object,
        source_id=source_id,
        confidence=confidence,
        proposer_agent=proposer_agent,
    )
    pg = get_pg_client()
    return await pg.propose_triple(triple)


# ---------------------------------------------------------------------------
# Critic-gated approval / rejection — the ONLY paths to Neo4j writes
# ---------------------------------------------------------------------------


@mcp.tool
async def list_pending_triples(limit: int = 50) -> list[dict[str, Any]]:
    """
    Return triples awaiting Critic decision (oldest first).
    Used by the Critic on its tick. Capped at 200.
    """
    limit = max(1, min(limit, 200))
    pg = get_pg_client()
    pending = await pg.list_pending_triples(limit=limit)
    return [t.model_dump(mode="json") for t in pending]


@mcp.tool
async def approve_triple(triple_id: str) -> dict[str, Any]:
    """
    Critic-gated approval. Atomically marks the pending triple approved AND
    commits the relationship to Neo4j with full provenance.

    This is the ONLY path through which triples reach the knowledge graph.

    Args:
        triple_id: UUID string of the pending triple (from list_pending_triples).

    Returns:
        {committed: bool, decision: "approved" | "race" | "approved_but_neo4j_failed"}
        - "race" means another caller decided this triple first
        - "approved_but_neo4j_failed" means PG was updated but Neo4j write
          failed; row is recoverable by a future reconciliation job
    """
    pg = get_pg_client()
    n = get_neo4j_client()
    tid = UUID(triple_id)

    pending = await pg.list_pending_triples(limit=200)
    target = next((t for t in pending if t.id == tid), None)
    if target is None:
        return {"committed": False, "decision": "race"}

    decided = await pg.mark_triple_decided(tid, TripleStatus.APPROVED)
    if not decided:
        return {"committed": False, "decision": "race"}

    try:
        await n.commit_triple_relationship(
            subject=target.subject,
            predicate=target.predicate,
            object_=target.object,
            source_id=target.source_id,
            confidence=target.confidence,
        )
    except Exception as exc:
        log.error(
            "approve_triple.neo4j_commit_failed",
            triple_id=str(tid),
            error=str(exc),
        )
        return {"committed": False, "decision": "approved_but_neo4j_failed"}

    log.info("approve_triple.committed", triple_id=str(tid))
    return {"committed": True, "decision": "approved"}


@mcp.tool
async def reject_triple(triple_id: str, reason: str) -> dict[str, Any]:
    """
    Critic-gated rejection. Marks the triple rejected with a reason.
    Nothing is written to Neo4j.

    Args:
        triple_id: UUID string of the pending triple.
        reason: Short reason (e.g., "faithfulness check failed",
                "predicate violates ontology", "low source reputation").
    """
    pg = get_pg_client()
    decided = await pg.mark_triple_decided(
        UUID(triple_id),
        TripleStatus.REJECTED,
        rejection_reason=reason,
    )
    return {
        "committed": False,
        "decision": "rejected" if decided else "race",
    }


# ---------------------------------------------------------------------------
# Mention tracking — Scouts write, Forecaster reads
# ---------------------------------------------------------------------------


@mcp.tool
async def record_mention(
    concept_name: str,
    source_id: str,
    source_type: str,
    observed_at: str,
) -> dict[str, bool]:
    """
    Record that a concept was mentioned in a source. Idempotent on
    (concept_name, source_id). Used by Scouts as they crawl sources.

    Args:
        concept_name: Concept that was mentioned.
        source_id: Stable source identifier.
        source_type: One of: arxiv | github | blog | spec | conference | rfc | other.
        observed_at: ISO 8601 timestamp.
    """
    pg = get_pg_client()
    await pg.record_mention(
        concept_name=concept_name,
        source_id=source_id,
        source_type=SourceType(source_type),
        observed_at=datetime.fromisoformat(observed_at),
    )
    return {"recorded": True}


@mcp.tool
async def get_mention_velocity(concept_name: str, window_days: int = 90) -> dict[str, Any]:
    """
    Weekly mention buckets and a slope (mentions/week trend) for a concept.
    Used by the Forecaster to detect rising-velocity concepts.

    velocity > 0 indicates increasing weekly mentions.
    Window capped at 365 days.
    """
    window_days = max(1, min(window_days, 365))
    pg = get_pg_client()
    return await pg.mention_velocity(concept_name, window_days=window_days)


# ---------------------------------------------------------------------------
# Graph traversal — Forecaster's primary read tool
# ---------------------------------------------------------------------------


@mcp.tool
async def traverse(start: str, edge_types: list[str], depth: int = 2) -> dict[str, Any]:
    """
    Multi-hop graph traversal from a starting Concept along the given edge types.

    Args:
        start: Starting concept name.
        edge_types: Allowed relationship types (e.g., ["SUPERSEDES", "COMPETES_WITH"]).
                    Each must match [A-Z][A-Z0-9_]{0,63}.
        depth: Max hop count (1-4, default 2).

    Returns:
        {"start": <str>, "depth": <int>, "edge_types": [...], "paths": [...]}
        where each path is {nodes: [...], relationships: [...]}.
        Limited to 100 paths.
    """
    if not edge_types:
        return {
            "start": start,
            "depth": depth,
            "edge_types": [],
            "paths": [],
        }
    for et in edge_types:
        if not _CYPHER_IDENT.match(et):
            raise ValueError(f"Invalid edge type {et!r}")
    depth = max(1, min(depth, 4))

    edge_pattern = "|".join(f"`{e}`" for e in edge_types)
    cypher = f"""
        MATCH path = (start:Concept {{name: $start}})
                     -[:{edge_pattern}*1..{depth}]-
                     (end:Concept)
        RETURN [n IN nodes(path) | properties(n)] AS nodes,
               [r IN relationships(path) | {{type: type(r), props: properties(r)}}] AS rels
        LIMIT 100
    """
    n = get_neo4j_client()
    async with n.session() as s:
        result = await s.run(cypher, start=start)
        rows = [r async for r in result]

    paths = [
        {
            "nodes": [_serialize_neo4j(node) for node in r["nodes"]],
            "relationships": [_serialize_neo4j(rel) for rel in r["rels"]],
        }
        for r in rows
    ]
    return {
        "start": start,
        "depth": depth,
        "edge_types": edge_types,
        "paths": paths,
    }


# ---------------------------------------------------------------------------
# Artifact storage — Scouts write raw text content
# ---------------------------------------------------------------------------


@mcp.tool
async def put_text_artifact(
    key: str, content: str, content_type: str = "text/plain"
) -> dict[str, str]:
    """
    Store a text artifact (RSS payload, paper abstract, README content)
    and return its s3:// URI for use as raw_artifact_uri on a Source.

    For binary content, use the agentradar-store S3 client directly;
    base64-roundtripping large blobs through MCP tool args is wasteful.
    """
    s3 = get_s3_client()
    uri = await s3.put_artifact(key, content.encode("utf-8"), content_type=content_type)
    return {"uri": uri}


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


@mcp.tool
async def healthcheck() -> dict[str, bool]:
    """Verify all backing stores are reachable. Useful as a smoke test."""
    n = get_neo4j_client()
    p = get_pg_client()
    s = get_s3_client()
    return {
        "neo4j": await n.healthcheck(),
        "postgres": await p.healthcheck(),
        "s3": await s.healthcheck(),
    }


# ---------------------------------------------------------------------------
# Forecasting Agent
# ---------------------------------------------------------------------------


@mcp.tool
async def propose_forecast(
    concept_name: str,
    claim: str,  # was 'prediction' in my earlier code
    confidence: float,
    confidence_band: str,
    horizon_months: int = 6,  # NEW — required by existing schema
    reasoning: str = "",
    cited_source_ids: list[str] | None = None,  # was 'cited_concept_ids'
    evidence_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Persist a forecast to the forecasts table.

    Args:
        concept_name: The concept being forecasted.
        claim: The prediction text.
        confidence: 0.0–1.0 self-assessed confidence.
        confidence_band: 'weak' | 'medium' | 'high'.
        horizon_months: 1–24 months out. Defaults to 6.
        reasoning: Why the Forecaster made this prediction.
        cited_source_ids: Source/concept IDs the prediction cites.
        evidence_snapshot: JSON dump of the evidence the Forecaster saw.

    Returns:
        {"forecast_id": str, "status": "stored"}
    """
    if not concept_name.strip():
        raise ValueError("concept_name required")
    if not claim.strip():
        raise ValueError("claim required")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be in [0,1], got {confidence}")
    if confidence_band not in ("weak", "medium", "high"):
        raise ValueError(f"confidence_band must be weak|medium|high, got {confidence_band!r}")
    if not (1 <= horizon_months <= 24):
        raise ValueError(f"horizon_months must be in [1,24], got {horizon_months}")

    pg = get_pg_client()
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO forecasts
                (concept_name, claim, confidence, confidence_band,
                 horizon_months, reasoning, cited_source_ids,
                 evidence_snapshot, predicted_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            RETURNING id
            """,
            concept_name,
            claim,
            confidence,
            confidence_band,
            horizon_months,
            reasoning,
            cited_source_ids or [],
            json.dumps(evidence_snapshot or {}),
        )
    return {"forecast_id": str(row["id"]), "status": "stored"}


@mcp.tool
async def list_recent_forecasts(limit: int = 10) -> dict[str, Any]:
    """
    Return the most recent forecasts. Used by the dashboard.
    """
    limit = max(1, min(limit, 100))
    pg = get_pg_client()
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, concept_name, claim, confidence, confidence_band,
                   horizon_months, reasoning, cited_source_ids, predicted_at,
                   outcome, graded_at
            FROM forecasts
            ORDER BY predicted_at DESC
            LIMIT $1
            """,
            limit,
        )
    forecasts = [
        {
            "forecast_id": str(r["id"]),
            "concept_name": r["concept_name"],
            "claim": r["claim"],
            "confidence": r["confidence"],
            "confidence_band": r["confidence_band"],
            "horizon_months": r["horizon_months"],
            "reasoning": r["reasoning"],
            "cited_source_ids": list(r["cited_source_ids"] or []),
            "predicted_at": r["predicted_at"].isoformat(),
            "outcome": r["outcome"],
            "graded_at": r["graded_at"].isoformat() if r["graded_at"] else None,
        }
        for r in rows
    ]
    return {"forecasts": forecasts, "count": len(forecasts)}


@mcp.tool
async def select_forecast_candidate(
    velocity_window_days: int = 90,
    cooldown_days: int = 14,
) -> dict[str, Any]:
    """
    Select the highest-velocity concept that hasn't been forecasted recently.

    Used by the Forecaster's auto-selection logic. Returns None when no
    candidate is available (empty graph, or all top concepts in cooldown).

    Args:
        velocity_window_days: How far back to count mentions for velocity ranking.
        cooldown_days: Concepts forecasted within this window are excluded.

    Returns:
        {"concept_name": <str>} when found,
        {"concept_name": null} when no candidate is available.
    """
    if not (1 <= velocity_window_days <= 365):
        raise ValueError(f"velocity_window_days must be in [1,365], got {velocity_window_days}")
    if not (0 <= cooldown_days <= 90):
        raise ValueError(f"cooldown_days must be in [0,90], got {cooldown_days}")

    pg = get_pg_client()
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH recent_forecasts AS (
                SELECT DISTINCT concept_name
                FROM forecasts
                WHERE predicted_at > NOW() - make_interval(days => $2)
            ),
            concept_volume AS (
                SELECT concept_name, COUNT(*)::int AS n
                FROM mention_events
                WHERE observed_at > NOW() - make_interval(days => $1)
                GROUP BY concept_name
            )
            SELECT concept_name
            FROM concept_volume
            WHERE concept_name NOT IN (SELECT concept_name FROM recent_forecasts)
            ORDER BY n DESC
            LIMIT 1
            """,
            velocity_window_days,
            cooldown_days,
        )
    return {"concept_name": row["concept_name"] if row else None}


@mcp.tool
async def get_forecast_evidence(
    concept_name: str,
    velocity_window_days: int = 90,
) -> dict[str, Any]:
    """
    Bundle all the evidence the Forecaster needs about one concept into a
    single MCP call. Reduces round-trips and keeps the agent-to-storage
    boundary clean (no direct PgClient access from agent code).

    Returns:
        {
          "concept_name": str,
          "total_mentions": int,
          "source_diversity": int,
          "mentions_by_source": {<source_type>: <count>, ...},
          "mention_velocity": {<velocity dict from PgClient.mention_velocity>},
        }
    """
    if not concept_name.strip():
        raise ValueError("concept_name required")
    if not (1 <= velocity_window_days <= 365):
        raise ValueError(f"velocity_window_days must be in [1,365], got {velocity_window_days}")

    pg = get_pg_client()

    # Mention velocity (existing method, time-bucketed)
    velocity = await pg.mention_velocity(
        concept_name,
        window_days=velocity_window_days,
    )

    # Mentions broken down by source type
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_type::text AS st, COUNT(*)::int AS n
            FROM mention_events
            WHERE concept_name = $1
            GROUP BY source_type
            """,
            concept_name,
        )
    mentions_by_source = {r["st"]: r["n"] for r in rows}
    total_mentions = sum(mentions_by_source.values())
    source_diversity = len(mentions_by_source)

    return {
        "concept_name": concept_name,
        "total_mentions": total_mentions,
        "source_diversity": source_diversity,
        "mentions_by_source": mentions_by_source,
        "mention_velocity": velocity,
    }


@mcp.tool
async def select_top_n_concepts(
    top_n: int = 5,
    velocity_window_days: int = 90,
    cooldown_days: int = 14,
) -> dict[str, Any]:
    """
    Return the top-N highest-velocity concepts that haven't been
    forecasted in the cooldown window. Used by the Planner for
    forecast.top_n / forecast.digest decomposition.

    Returns:
        {"concept_names": [<str>, ...]}  (may be empty if graph sparse)
    """
    if not (1 <= top_n <= 20):
        raise ValueError(f"top_n must be in [1,20], got {top_n}")
    if not (1 <= velocity_window_days <= 365):
        raise ValueError("velocity_window_days must be in [1,365]")
    if not (0 <= cooldown_days <= 90):
        raise ValueError("cooldown_days must be in [0,90]")

    pg = get_pg_client()
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH recent_forecasts AS (
                SELECT DISTINCT concept_name
                FROM forecasts
                WHERE predicted_at > NOW() - make_interval(days => $2)
            ),
            concept_volume AS (
                SELECT concept_name, COUNT(*)::int AS n
                FROM mention_events
                WHERE observed_at > NOW() - make_interval(days => $1)
                GROUP BY concept_name
            )
            SELECT concept_name
            FROM concept_volume
            WHERE concept_name NOT IN (SELECT concept_name FROM recent_forecasts)
            ORDER BY n DESC
            LIMIT $3
            """,
            velocity_window_days,
            cooldown_days,
            top_n,
        )
    return {"concept_names": [r["concept_name"] for r in rows]}


@mcp.tool
async def propose_digest(
    label: str,
    themes: str,
    standout: str,
    forecasts: list[dict],
    average_confidence: float,
    confidence_band: str,
) -> dict[str, Any]:
    """
    Persist a weekly digest. Each digest references N forecast rows.

    The forecasts JSON snapshot is stored alongside the digest so the
    digest is reproducible later even if individual forecast rows get
    superseded.
    """
    if not (0.0 <= average_confidence <= 1.0):
        raise ValueError(f"average_confidence must be in [0,1], got {average_confidence}")
    if confidence_band not in ("weak", "medium", "high"):
        raise ValueError(f"confidence_band must be weak|medium|high, got {confidence_band!r}")

    pg = get_pg_client()
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO digests
                (label, themes, standout, forecasts_snapshot,
                 average_confidence, confidence_band, generated_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            RETURNING id
            """,
            label,
            themes,
            standout,
            json.dumps(forecasts),
            average_confidence,
            confidence_band,
        )
    return {"digest_id": str(row["id"]), "status": "stored"}


@mcp.tool
async def list_recent_digests(limit: int = 5) -> dict[str, Any]:
    """Return the most recent digests, newest first."""
    limit = max(1, min(limit, 50))
    pg = get_pg_client()
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, label, themes, standout, forecasts_snapshot,
                   average_confidence, confidence_band, generated_at
            FROM digests
            ORDER BY generated_at DESC
            LIMIT $1
            """,
            limit,
        )
    return {
        "digests": [
            {
                "digest_id": str(r["id"]),
                "label": r["label"],
                "themes": r["themes"],
                "standout": r["standout"],
                "forecasts": json.loads(r["forecasts_snapshot"]),
                "average_confidence": float(r["average_confidence"]),
                "confidence_band": r["confidence_band"],
                "generated_at": r["generated_at"].isoformat(),
            }
            for r in rows
        ],
        "count": len(rows),
    }
