"""
Integration tests for Session 2 work against the TEST data plane.

Bring up the test plane first:
    ./scripts/test-up.sh

Run:
    uv run pytest -m integration

Tests use fixtures from tests/integration/conftest.py that connect
to localhost:5433 (postgres-test) and bolt://localhost:7688
(neo4j-test) — your dev data plane is untouched.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def seed_mentions(test_pg_conn):
    """Seed mention_events into the TEST Postgres."""
    prefix = f"test_session2_{uuid.uuid4().hex[:8]}"
    concepts = [f"{prefix}_HIGH", f"{prefix}_MID", f"{prefix}_LOW"]
    try:
        for concept, count in zip(concepts, [10, 5, 2], strict=False):
            for _ in range(count):
                await test_pg_conn.execute(
                    """
                    INSERT INTO mention_events
                        (concept_name, source_id, source_type, observed_at)
                    VALUES ($1, $2, 'arxiv', NOW())
                    """,
                    concept,
                    str(uuid.uuid4()),
                )
        yield concepts
    finally:
        # Cleanup is still useful even though the fixture wipes between tests,
        # because pytest-asyncio doesn't guarantee fixture ordering.
        for c in concepts:
            await test_pg_conn.execute(
                "DELETE FROM mention_events WHERE concept_name = $1",
                c,
            )
            await test_pg_conn.execute(
                "DELETE FROM forecasts WHERE concept_name = $1",
                c,
            )


class TestPostgresQueries:
    """Direct SQL tests against test_pg_conn. These don't go through MCP
    or the api process; they verify the Postgres-side schema and queries
    work correctly. MCP-via-api tests are deferred to a future session
    when we add api-test to docker-compose.test.yml."""

    @pytest.mark.asyncio
    async def test_mention_events_table_exists(self, test_pg_conn):
        result = await test_pg_conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables " "WHERE table_name = 'mention_events'"
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_digests_table_exists(self, test_pg_conn):
        result = await test_pg_conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables " "WHERE table_name = 'digests'"
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_seed_mentions_creates_rows(self, seed_mentions, test_pg_conn):
        rows = await test_pg_conn.fetch(
            "SELECT concept_name, COUNT(*) AS n FROM mention_events "
            "WHERE concept_name = ANY($1) GROUP BY concept_name",
            seed_mentions,
        )
        counts = {r["concept_name"]: r["n"] for r in rows}
        assert counts[seed_mentions[0]] == 10  # _HIGH
        assert counts[seed_mentions[1]] == 5  # _MID
        assert counts[seed_mentions[2]] == 2  # _LOW


class TestNeo4jSchema:
    """Verify test Neo4j has the schema we expect after init runs."""

    @pytest.mark.asyncio
    async def test_concept_name_unique_constraint_exists(self, test_neo4j_session):
        result = await test_neo4j_session.run(
            "SHOW CONSTRAINTS YIELD name WHERE name = 'concept_name_unique' "
            "RETURN count(name) AS n"
        )
        record = await result.single()
        assert record["n"] == 1

    @pytest.mark.asyncio
    async def test_neo4j_starts_empty_per_test(self, test_neo4j_session):
        """Fixture should wipe Neo4j before each test."""
        result = await test_neo4j_session.run("MATCH (n) RETURN count(n) AS n")
        record = await result.single()
        assert record["n"] == 0
