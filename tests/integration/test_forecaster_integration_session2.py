"""
Integration tests for Session 2 work against the live data plane.

These run only with `pytest -m integration` and require:
- docker compose up -d (postgres + neo4j + minio + api healthy)
- The forecasts and digests tables present (init scripts applied)

The tests insert their own mention_events fixtures so they don't depend
on Scout-produced data being in the DB at test time.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastmcp import Client

MCP_URL = "http://localhost:8000/mcp/"

pytestmark = pytest.mark.integration


@pytest.fixture
async def mcp_client():
    """A fresh MCP client per test. Auto-closes on exit."""
    async with Client(MCP_URL) as c:
        yield c


@pytest.fixture
async def seed_mentions():
    """
    Insert mention_events for three test concepts with distinguishable
    counts so velocity-ordering is predictable. Cleanup is by-prefix
    so other test runs aren't disturbed.

    Each mention gets a fresh source_id because mention_events has a
    composite primary key on (concept_name, source_id) — re-using the
    same source_id would fail with unique-constraint violations.
    """
    import asyncpg
    conn = await asyncpg.connect(
        host="localhost", port=5432,
        user="agentradar", password="agentradar_dev", database="agentradar",
    )
    prefix = f"test_session2_{uuid.uuid4().hex[:8]}"
    concepts = [f"{prefix}_HIGH", f"{prefix}_MID", f"{prefix}_LOW"]
    try:
        for concept, count in zip(concepts, [10, 5, 2]):
            for _ in range(count):
                await conn.execute(
                    """
                    INSERT INTO mention_events
                        (concept_name, source_id, source_type, observed_at)
                    VALUES ($1, $2, 'arxiv', NOW())
                    """,
                    concept, str(uuid.uuid4()),
                )
        yield concepts
    finally:
        for c in concepts:
            await conn.execute(
                "DELETE FROM mention_events WHERE concept_name = $1", c,
            )
            await conn.execute(
                "DELETE FROM forecasts WHERE concept_name = $1", c,
            )
        await conn.close()
        await conn.close()


# ---- New Session 2 MCP tools ------------------------------------------


class TestSelectTopnConcepts:
    @pytest.mark.asyncio
    async def test_select_top_n_returns_concepts_ordered_by_volume(
        self, mcp_client, seed_mentions,
    ):
        result = await mcp_client.call_tool("select_top_n_concepts", {
            "top_n": 20,
            "velocity_window_days": 90,
            "cooldown_days": 14,
        })
        names = result.data["concept_names"]
        # Our seeded concepts should appear, ordered HIGH > MID > LOW
        seeded = [n for n in names if n in seed_mentions]
        assert len(seeded) == 3
        assert seeded[0].endswith("_HIGH")
        assert seeded[1].endswith("_MID")
        assert seeded[2].endswith("_LOW")

    @pytest.mark.asyncio
    async def test_select_top_n_respects_top_n_limit(self, mcp_client):
        result = await mcp_client.call_tool("select_top_n_concepts", {
            "top_n": 2,
        })
        names = result.data["concept_names"]
        assert len(names) <= 2

    @pytest.mark.asyncio
    async def test_select_top_n_rejects_invalid_top_n(self, mcp_client):
        with pytest.raises(Exception):  # fastmcp wraps as ToolError
            await mcp_client.call_tool("select_top_n_concepts", {
                "top_n": 50,  # over the 20 limit
            })


class TestGetForecastEvidence:
    @pytest.mark.asyncio
    async def test_get_evidence_returns_full_shape(
        self, mcp_client, seed_mentions,
    ):
        concept = seed_mentions[0]  # _HIGH, has 10 mentions
        result = await mcp_client.call_tool("get_forecast_evidence", {
            "concept_name": concept,
            "velocity_window_days": 90,
        })
        evidence = result.data
        assert evidence["concept_name"] == concept
        assert evidence["total_mentions"] == 10
        assert evidence["source_diversity"] == 1
        assert evidence["mentions_by_source"] == {"arxiv": 10}
        assert "mention_velocity" in evidence

    @pytest.mark.asyncio
    async def test_get_evidence_empty_concept_name_rejected(self, mcp_client):
        with pytest.raises(Exception):
            await mcp_client.call_tool("get_forecast_evidence", {
                "concept_name": "",
            })


class TestDigestPersistence:
    @pytest.mark.asyncio
    async def test_propose_digest_persists_and_list_returns_it(self, mcp_client):
        label = f"integration_test_{uuid.uuid4().hex[:8]}"
        # Persist
        result = await mcp_client.call_tool("propose_digest", {
            "label": label,
            "themes": "Test themes paragraph",
            "standout": "Test standout pick",
            "forecasts": [
                {"concept_name": "TestConcept", "prediction": "test", "confidence": 0.6},
            ],
            "average_confidence": 0.6,
            "confidence_band": "medium",
        })
        assert result.data["status"] == "stored"
        digest_id = result.data["digest_id"]

        # Verify in list
        listing = await mcp_client.call_tool("list_recent_digests", {"limit": 50})
        labels = [d["label"] for d in listing.data["digests"]]
        assert label in labels

        # Cleanup
        import asyncpg
        conn = await asyncpg.connect(
            host="localhost", port=5432,
            user="agentradar", password="agentradar_dev", database="agentradar",
        )
        try:
            await conn.execute("DELETE FROM digests WHERE id = $1::uuid", digest_id)
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_propose_digest_rejects_invalid_band(self, mcp_client):
        with pytest.raises(Exception):
            await mcp_client.call_tool("propose_digest", {
                "label": "x",
                "themes": "t",
                "standout": "s",
                "forecasts": [],
                "average_confidence": 0.5,
                "confidence_band": "GARBAGE",  # not weak/medium/high
            })

    @pytest.mark.asyncio
    async def test_propose_digest_rejects_oob_confidence(self, mcp_client):
        with pytest.raises(Exception):
            await mcp_client.call_tool("propose_digest", {
                "label": "x",
                "themes": "t",
                "standout": "s",
                "forecasts": [],
                "average_confidence": 1.5,  # > 1.0
                "confidence_band": "high",
            })