"""Integration tests against a real Postgres + pgvector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentradar_core import SourceType, Triple, TripleStatus


pytestmark = pytest.mark.integration


class TestPostgresHealthcheck:
    async def test_healthcheck_passes(self, pg_client) -> None:
        assert await pg_client.healthcheck() is True


class TestProposeTriple:
    async def test_first_proposal_inserts(self, clean_pg) -> None:
        triple = Triple(
            subject="MCP", predicate="INTRODUCED_BY", object="Anthropic",
            source_id="src-1", confidence=0.8, proposer_agent="scout",
        )
        result = await clean_pg.propose_triple(triple)

        assert result["status"] == "pending"
        pending = await clean_pg.list_pending_triples()
        assert len(pending) == 1
        assert pending[0].subject == "MCP"

    async def test_duplicate_proposal_is_idempotent(self, clean_pg) -> None:
        """Re-proposing same triple shouldn't create a second pending row."""
        triple = Triple(
            subject="A", predicate="X", object="B",
            source_id="s1", confidence=0.5, proposer_agent="scout",
        )
        await clean_pg.propose_triple(triple)
        await clean_pg.propose_triple(triple)

        pending = await clean_pg.list_pending_triples()
        assert len(pending) == 1

    async def test_duplicate_with_higher_confidence_wins(self, clean_pg) -> None:
        triple_low = Triple(
            subject="A", predicate="X", object="B",
            source_id="s1", confidence=0.3, proposer_agent="scout",
        )
        triple_high = Triple(
            subject="A", predicate="X", object="B",
            source_id="s1", confidence=0.9, proposer_agent="scout",
        )
        await clean_pg.propose_triple(triple_low)
        await clean_pg.propose_triple(triple_high)

        pending = await clean_pg.list_pending_triples()
        assert len(pending) == 1
        assert pending[0].confidence == 0.9


class TestCriticDecisions:
    async def test_approve_marks_decided(self, clean_pg) -> None:
        triple = Triple(
            subject="A", predicate="X", object="B",
            source_id="s1", confidence=0.7, proposer_agent="scout",
        )
        result = await clean_pg.propose_triple(triple)
        from uuid import UUID
        decided = await clean_pg.mark_triple_decided(
            UUID(result["triple_id"]), TripleStatus.APPROVED
        )
        assert decided is True

        # No longer in pending list:
        assert await clean_pg.list_pending_triples() == []

    async def test_reject_records_reason(self, clean_pg) -> None:
        from uuid import UUID
        triple = Triple(
            subject="A", predicate="X", object="B",
            source_id="s1", confidence=0.7, proposer_agent="scout",
        )
        result = await clean_pg.propose_triple(triple)
        await clean_pg.mark_triple_decided(
            UUID(result["triple_id"]),
            TripleStatus.REJECTED,
            rejection_reason="failed faithfulness check",
        )

        # Verify directly in DB:
        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, rejection_reason FROM pending_triples WHERE id = $1",
                UUID(result["triple_id"]),
            )
        assert row["status"] == "rejected"
        assert row["rejection_reason"] == "failed faithfulness check"

    async def test_decide_already_decided_returns_false(self, clean_pg) -> None:
        """Race protection: second decision attempt should be a no-op."""
        from uuid import UUID
        triple = Triple(
            subject="A", predicate="X", object="B",
            source_id="s1", confidence=0.7, proposer_agent="scout",
        )
        result = await clean_pg.propose_triple(triple)
        triple_id = UUID(result["triple_id"])

        first = await clean_pg.mark_triple_decided(triple_id, TripleStatus.APPROVED)
        second = await clean_pg.mark_triple_decided(triple_id, TripleStatus.REJECTED)

        assert first is True
        assert second is False


class TestMentionVelocity:
    async def test_record_and_compute(self, clean_pg) -> None:
        now = datetime.now(UTC)
        for i in range(5):
            await clean_pg.record_mention(
                concept_name="MCP",
                source_id=f"src-{i}",
                source_type=SourceType.ARXIV,
                observed_at=now - timedelta(days=i * 7),  # one per week back
            )

        v = await clean_pg.mention_velocity("MCP", window_days=90)
        assert v["concept"] == "MCP"
        assert len(v["buckets"]) >= 1
        assert isinstance(v["velocity"], float)

    async def test_record_mention_idempotent(self, clean_pg) -> None:
        now = datetime.now(UTC)
        await clean_pg.record_mention("X", "src-1", SourceType.GITHUB, now)
        await clean_pg.record_mention("X", "src-1", SourceType.GITHUB, now)

        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM mention_events WHERE concept_name = 'X'"
            )
        assert count == 1


class TestPgVector:
    async def test_upsert_and_search(self, clean_pg) -> None:
        # Embedding dim must match the schema (1024 by default).
        # Use clearly-distinguishable vectors for deterministic ordering.
        from agentradar_core import settings
        dim = settings.embedding.dim

        await clean_pg.upsert_embedding(
            "MCP", [1.0] + [0.0] * (dim - 1), description="Model Context Protocol"
        )
        await clean_pg.upsert_embedding(
            "ANP", [0.0, 1.0] + [0.0] * (dim - 2), description="Agent Network Protocol"
        )

        # Query that points strongly toward MCP's vector
        query = [1.0] + [0.0] * (dim - 1)
        results = await clean_pg.search_similar_concepts(query, limit=2)

        assert len(results) == 2
        assert results[0]["concept_name"] == "MCP"  # closest
        assert results[0]["similarity"] > results[1]["similarity"]