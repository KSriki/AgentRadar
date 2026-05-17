"""
Integration tests for race semantics on Critic decisions.

mark_triple_decided uses WHERE status='pending' so only one of N
concurrent decisions wins. These tests exercise that property with
real concurrency and assert exactly-once-wins.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from agentradar_core import Triple, TripleStatus


def _t(subject: str, source_id: str) -> Triple:
    return Triple(
        subject=subject,
        predicate="MENTIONED_IN",
        object=source_id,
        source_id=source_id,
        confidence=0.5,
        proposer_agent="test",
    )


@pytest.mark.integration
class TestRaceSemantics:
    """Concurrent decisions on the same triple — exactly one wins."""

    @pytest.mark.asyncio
    async def test_two_concurrent_approves_exactly_one_wins(self, clean_pg):
        proposal = await clean_pg.propose_triple(_t("X", "src:race1"))
        triple_id = uuid.UUID(proposal["triple_id"])

        results = await asyncio.gather(
            clean_pg.mark_triple_decided(triple_id, TripleStatus.APPROVED, None),
            clean_pg.mark_triple_decided(triple_id, TripleStatus.APPROVED, None),
        )

        assert results.count(True) == 1
        assert results.count(False) == 1

    @pytest.mark.asyncio
    async def test_approve_then_reject_second_loses(self, clean_pg):
        proposal = await clean_pg.propose_triple(_t("Y", "src:race2"))
        triple_id = uuid.UUID(proposal["triple_id"])

        ok1 = await clean_pg.mark_triple_decided(
            triple_id,
            TripleStatus.APPROVED,
            None,
        )
        ok2 = await clean_pg.mark_triple_decided(
            triple_id,
            TripleStatus.REJECTED,
            "overriding approve",
        )

        assert ok1 is True
        assert ok2 is False

        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM pending_triples WHERE id = $1",
                triple_id,
            )
        assert row["status"] == "approved"

    @pytest.mark.asyncio
    async def test_many_concurrent_decisions_distributed_across_triples(self, clean_pg):
        """20 distinct triples, 20 concurrent decisions, all should succeed."""
        triple_ids = []
        for i in range(20):
            p = await clean_pg.propose_triple(_t(f"S{i}", f"src:multi:{i}"))
            triple_ids.append(uuid.UUID(p["triple_id"]))

        results = await asyncio.gather(
            *[clean_pg.mark_triple_decided(tid, TripleStatus.APPROVED, None) for tid in triple_ids]
        )

        assert all(results)

        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM pending_triples WHERE status = 'approved'"
            )
        assert count == 20

    @pytest.mark.asyncio
    async def test_decide_nonexistent_triple_returns_false(self, clean_pg):
        bogus_id = uuid.uuid4()
        ok = await clean_pg.mark_triple_decided(
            bogus_id,
            TripleStatus.APPROVED,
            None,
        )
        assert ok is False
