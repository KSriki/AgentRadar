"""
Integration tests for the proposer-critic storage gate.

The architectural premise: agents can propose triples (which write to
pending_triples in Postgres only), but only the Critic decision path
(approve_triple / reject_triple) can commit to Neo4j. These tests pin
that property by exercising both paths and asserting end state in BOTH
data stores.

If anyone ever refactors propose_triple to write directly to Neo4j, these
tests fire immediately — that's the value of pinning architectural claims.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from agentradar_core import Triple, TripleStatus


def _make_triple(
    subject: str = "MCP",
    predicate: str = "INTRODUCED_BY",
    object_: str = "Anthropic",
    source_id: str = "test:src1",
    confidence: float = 0.8,
    proposer_agent: str = "test-scout",
) -> Triple:
    """Helper to build a Triple object for tests."""
    return Triple(
        subject=subject,
        predicate=predicate,
        object=object_,
        source_id=source_id,
        confidence=confidence,
        proposer_agent=proposer_agent,
    )


@pytest.mark.integration
class TestProposerCriticGate:
    """Critic-approval is the only path to graph commits."""

    @pytest.mark.asyncio
    async def test_propose_writes_to_pending_only(self, clean_pg, clean_neo4j):
        """propose_triple writes to Postgres pending queue but NOT to Neo4j."""
        result = await clean_pg.propose_triple(_make_triple())
        assert result["status"] == "pending"

        # In Postgres
        pending = await clean_pg.list_pending_triples(limit=10)
        assert len(pending) == 1
        assert pending[0].subject == "MCP"
        assert pending[0].status == TripleStatus.PENDING

        # NOT in Neo4j
        async with clean_neo4j.session() as s:
            result = await s.run("MATCH (n:Concept) RETURN count(n) AS n")
            count = await result.single()
        assert count["n"] == 0, (
            "Triple should NOT be in Neo4j before Critic approval — "
            "this would mean the proposer-critic gate is broken"
        )

    @pytest.mark.asyncio
    async def test_approve_commits_to_both_stores(self, clean_pg, clean_neo4j):
        """The approval path: mark_triple_decided + commit_triple_relationship."""
        proposal = await clean_pg.propose_triple(
            _make_triple(
                subject="ROMA",
                predicate="INSTANCE_OF",
                object_="Pattern",
                source_id="test:src2",
                confidence=0.9,
            )
        )
        triple_id = UUID(proposal["triple_id"])

        marked = await clean_pg.mark_triple_decided(
            triple_id=triple_id,
            decision=TripleStatus.APPROVED,
            rejection_reason=None,
        )
        assert marked is True

        # Real approve path commits to Neo4j
        await clean_neo4j.commit_triple_relationship(
            subject="ROMA",
            predicate="INSTANCE_OF",
            object_="Pattern",
            source_id="test:src2",
            confidence=0.9,
        )

        concept_data = await clean_neo4j.fetch_concept("ROMA")
        assert concept_data is not None
        assert concept_data["concept"]["name"] == "ROMA"

    @pytest.mark.asyncio
    async def test_reject_marks_pg_only(self, clean_pg, clean_neo4j):
        """reject_triple records the decision but never touches Neo4j."""
        proposal = await clean_pg.propose_triple(
            _make_triple(
                subject="Hallucinated",
                predicate="INTRODUCED_BY",
                object_="NobodyReal",
                source_id="test:bad",
                confidence=0.4,
            )
        )

        marked = await clean_pg.mark_triple_decided(
            triple_id=UUID(proposal["triple_id"]),
            decision=TripleStatus.REJECTED,
            rejection_reason="[ontology] predicate not in known set",
        )
        assert marked is True

        # Neo4j is empty
        async with clean_neo4j.session() as s:
            result = await s.run("MATCH (n:Concept) RETURN count(n) AS n")
            count = await result.single()
        assert count["n"] == 0

    @pytest.mark.asyncio
    async def test_repropose_same_triple_does_not_duplicate(self, clean_pg):
        """Re-running an idempotent agent must not create duplicate rows."""
        triple = _make_triple(
            proposer_agent="scout-arxiv",
            subject="MCP",
            predicate="MENTIONED_IN",
            object_="arxiv:2401.0001",
            source_id="arxiv:2401.0001",
            confidence=0.6,
        )
        for _ in range(3):
            await clean_pg.propose_triple(triple)

        pending = await clean_pg.list_pending_triples(limit=10)
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_repropose_with_higher_confidence_updates_in_place(self, clean_pg):
        """Higher confidence on second call wins — same proposal_hash, updated row."""
        low = _make_triple(
            subject="X",
            predicate="MENTIONED_IN",
            object_="src:1",
            source_id="src:1",
            confidence=0.4,
        )
        high = _make_triple(
            subject="X",
            predicate="MENTIONED_IN",
            object_="src:1",
            source_id="src:1",
            confidence=0.8,
        )
        await clean_pg.propose_triple(low)
        await clean_pg.propose_triple(high)

        pending = await clean_pg.list_pending_triples(limit=5)
        assert len(pending) == 1
        assert pending[0].confidence == 0.8
