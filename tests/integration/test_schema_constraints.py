"""
Integration tests for schema constraint enforcement.

These verify that init-sidecar-applied schema constraints are actually
enforced at runtime — the proposer-critic gate's idempotency depends
on UNIQUE constraints, the graph's structure depends on Neo4j's
uniqueness rules.
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from agentradar_core import SourceType, Triple


@pytest.mark.integration
class TestPostgresUniqueness:
    """UNIQUE constraints in pending_triples and mention_events."""

    @pytest.mark.asyncio
    async def test_proposal_hash_is_unique(self, clean_pg):
        """The same triple content always produces the same proposal_hash;
        the UNIQUE constraint stops duplicate rows."""
        triple = Triple(
            subject="X", predicate="MENTIONED_IN", object="src:1",
            source_id="src:1", confidence=0.5, proposer_agent="test",
        )

        result = await clean_pg.propose_triple(triple)
        triple_id = result["triple_id"]

        # Read the proposal_hash and try to forcibly insert another row with it
        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT proposal_hash FROM pending_triples WHERE id = $1::uuid",
                triple_id,
            )
            assert existing is not None
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO pending_triples
                        (proposer_agent, subject, predicate, object,
                         source_id, confidence, status, proposal_hash)
                    VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
                    """,
                    "different-proposer", "X", "MENTIONED_IN", "src:1",
                    "src:1", 0.7, existing["proposal_hash"],
                )

    @pytest.mark.asyncio
    async def test_mention_unique_on_concept_and_source(self, clean_pg):
        """mention_events UNIQUE(concept_name, source_id) makes record_mention idempotent."""
        for _ in range(2):
            await clean_pg.record_mention(
                concept_name="MCP",
                source_id="arxiv:2401.99999",
                source_type=SourceType.ARXIV,
                observed_at=datetime.now(UTC),
            )

        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM mention_events "
                "WHERE concept_name = $1 AND source_id = $2",
                "MCP", "arxiv:2401.99999",
            )
        assert count == 1


@pytest.mark.integration
class TestNeo4jUniqueness:
    """Constraints applied via init-sidecar Cypher."""

    @pytest.mark.asyncio
    async def test_concept_name_is_unique(self, clean_neo4j):
        """Two MERGE statements with same name yield one node, not two."""
        async with clean_neo4j.session() as s:
            await s.run("MERGE (c:Concept {name: $name})", name="MCP")
            await s.run("MERGE (c:Concept {name: $name})", name="MCP")

            result = await s.run(
                "MATCH (c:Concept {name: $name}) RETURN count(c) AS n",
                name="MCP",
            )
            count = await result.single()
        assert count["n"] == 1

    @pytest.mark.asyncio
    async def test_concept_unique_constraint_exists(self, clean_neo4j):
        """The UNIQUE constraint should be visible in SHOW CONSTRAINTS."""
        async with clean_neo4j.session() as s:
            result = await s.run("SHOW CONSTRAINTS")
            constraints = [dict(r) async for r in result]

        concept_unique = [
            c for c in constraints
            if "Concept" in str(c) and "name" in str(c) and (
                "UNIQUE" in str(c).upper() or "uniqueness" in str(c).lower()
            )
        ]
        assert len(concept_unique) >= 1, (
            f"No UNIQUE constraint on Concept.name found. "
            f"Constraints visible: {constraints}"
        )