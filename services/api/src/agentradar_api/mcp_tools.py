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

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from fastmcp import FastMCP

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
        raise ValueError(
            f"Invalid predicate {predicate!r}: must match [A-Z][A-Z0-9_]{{0,63}}"
        )
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
async def get_mention_velocity(
    concept_name: str, window_days: int = 90
) -> dict[str, Any]:
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
async def traverse(
    start: str, edge_types: list[str], depth: int = 2
) -> list[dict[str, Any]]:
    """
    Multi-hop graph traversal from a starting Concept along the given edge types.

    Args:
        start: Starting concept name.
        edge_types: Allowed relationship types (e.g., ["SUPERSEDES", "COMPETES_WITH"]).
                    Each must match [A-Z][A-Z0-9_]{0,63}.
        depth: Max hop count (1-4, default 2).

    Returns:
        List of paths, each {nodes: [...], relationships: [...]}. Limited to 100.
    """
    if not edge_types:
        return []
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
    return [{"nodes": r["nodes"], "relationships": r["rels"]} for r in rows]


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
    uri = await s3.put_artifact(
        key, content.encode("utf-8"), content_type=content_type
    )
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



