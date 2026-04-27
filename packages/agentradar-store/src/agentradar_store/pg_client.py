"""
Async Postgres client backed by asyncpg.

asyncpg manages a connection pool internally; we just hold one Pool per process.
Higher-level helpers live here for the proposer-critic queue and mention velocity
queries — these are hot paths called by multiple agents.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from agentradar_core import (
    PendingTriple,
    PostgresSettings,
    SourceType,
    Triple,
    TripleStatus,
    get_logger,
    settings,
)

log = get_logger(__name__)


class PgClient:
    def __init__(self, cfg: PostgresSettings) -> None:
        self._cfg = cfg
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._cfg.dsn.get_secret_value(),
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        log.info("postgres.connected")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("postgres.closed")

    async def _ensure(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.connect()
        assert self._pool is not None
        return self._pool

    # ----- proposer-critic queue ------------------------------------------

    @staticmethod
    def hash_triple(subject: str, predicate: str, object_: str, source_id: str) -> str:
        """Deterministic hash for idempotency on triple proposals."""
        return hashlib.sha256(
            f"{subject}|{predicate}|{object_}|{source_id}".encode()
        ).hexdigest()

    async def propose_triple(self, triple: Triple) -> dict[str, Any]:
        """
        Insert a triple into pending_triples. Idempotent on (subject, predicate,
        object, source_id) — re-proposing only updates confidence upward.

        Returns {triple_id, status} so the caller knows whether this is a fresh
        proposal or an update to an existing one.
        """
        proposal_hash = self.hash_triple(
            triple.subject, triple.predicate, triple.object, triple.source_id
        )
        pool = await self._ensure()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO pending_triples
                    (proposer_agent, subject, predicate, object,
                     source_id, confidence, proposal_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (proposal_hash) DO UPDATE
                    SET confidence = GREATEST(
                        pending_triples.confidence,
                        EXCLUDED.confidence
                    )
                RETURNING id, status
                """,
                triple.proposer_agent,
                triple.subject,
                triple.predicate,
                triple.object,
                triple.source_id,
                triple.confidence,
                proposal_hash,
            )
        return {"triple_id": str(row["id"]), "status": row["status"]}

    async def list_pending_triples(self, limit: int = 50) -> list[PendingTriple]:
        pool = await self._ensure()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, proposer_agent, subject, predicate, object,
                       source_id, confidence, proposal_hash, status,
                       rejection_reason, created_at, decided_at
                FROM pending_triples
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT $1
                """,
                limit,
            )
        return [PendingTriple(**dict(r)) for r in rows]

    async def mark_triple_decided(
        self,
        triple_id: UUID,
        decision: TripleStatus,
        rejection_reason: str | None = None,
    ) -> bool:
        """
        Update a pending triple to approved/rejected. Returns False if the row
        was already decided (race protection).
        """
        if decision == TripleStatus.PENDING:
            raise ValueError("decision must be approved or rejected")
        pool = await self._ensure()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE pending_triples
                SET status = $2,
                    rejection_reason = $3,
                    decided_at = NOW()
                WHERE id = $1 AND status = 'pending'
                RETURNING id
                """,
                triple_id,
                decision.value,
                rejection_reason,
            )
        return row is not None

    # ----- mention velocity (used by Novelty Detector + Forecaster) -------

    async def record_mention(
        self,
        concept_name: str,
        source_id: str,
        source_type: SourceType,
        observed_at: datetime,
    ) -> None:
        """Append-only mention event. Idempotent on (concept_name, source_id)."""
        pool = await self._ensure()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mention_events (concept_name, source_id, source_type, observed_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (concept_name, source_id) DO NOTHING
                """,
                concept_name,
                source_id,
                source_type.value,
                observed_at,
            )

    async def mention_velocity(
        self, concept_name: str, window_days: int = 90
    ) -> dict[str, Any]:
        """Weekly mention buckets + a simple slope (mentions/week trend)."""
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        pool = await self._ensure()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT date_trunc('week', observed_at) AS week,
                       COUNT(*)::int                  AS mentions
                FROM mention_events
                WHERE concept_name = $1 AND observed_at >= $2
                GROUP BY week
                ORDER BY week
                """,
                concept_name,
                cutoff,
            )
        buckets = [
            {"week": r["week"].isoformat(), "mentions": r["mentions"]} for r in rows
        ]
        velocity = _slope([r["mentions"] for r in rows])
        return {
            "concept": concept_name,
            "window_days": window_days,
            "buckets": buckets,
            "velocity": velocity,
        }

    # ----- pgvector (concept embeddings) ----------------------------------

    async def upsert_embedding(
        self, concept_name: str, embedding: list[float], description: str | None = None
    ) -> None:
        pool = await self._ensure()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO concept_embeddings (concept_name, embedding, description)
                VALUES ($1, $2, $3)
                ON CONFLICT (concept_name) DO UPDATE
                    SET embedding   = EXCLUDED.embedding,
                        description = COALESCE(EXCLUDED.description,
                                               concept_embeddings.description),
                        updated_at  = NOW()
                """,
                concept_name,
                _vec(embedding),
                description,
            )

    async def search_similar_concepts(
        self, embedding: list[float], limit: int = 10
    ) -> list[dict[str, Any]]:
        """Cosine-similarity search. Higher 'similarity' = closer match."""
        pool = await self._ensure()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT concept_name,
                       description,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM concept_embeddings
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                _vec(embedding),
                limit,
            )
        return [dict(r) for r in rows]

    async def healthcheck(self) -> bool:
        try:
            pool = await self._ensure()
            async with pool.acquire() as conn:
                val = await conn.fetchval("SELECT 1")
            return val == 1
        except Exception as exc:
            log.warning("postgres.healthcheck_failed", error=str(exc))
            return False


# ---- helpers ---------------------------------------------------------------


def _slope(values: list[int]) -> float:
    """
    Trivial linear-regression slope on weekly counts.
    Positive = mentions increasing week-over-week. Returns 0 for <2 points.

    This is intentionally simple; we'll replace with a real time-series
    method (e.g., Mann-Kendall) when the Forecaster gets serious.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den else 0.0


def _vec(embedding: list[float]) -> str:
    """
    Format a Python list as the pgvector textual representation: '[1.0,2.0,...]'.
    Sending a Python list directly to asyncpg requires a registered codec; the
    string form works without any setup.
    """
    return "[" + ",".join(f"{x}" for x in embedding) + "]"


# ---- module-level singleton -----------------------------------------------

_singleton: PgClient | None = None


def get_pg_client() -> PgClient:
    global _singleton
    if _singleton is None:
        _singleton = PgClient(settings.postgres)
    return _singleton