"""
Integration-test conftest. Two responsibilities:

1. Manage the test data plane lifecycle. The session-scoped fixture
   `_test_data_plane` (autouse) brings up postgres-test + neo4j-test
   at session start and tears them down at session end. Detect-and-reuse:
   if the containers are already up from a prior interrupted session,
   reuse them rather than tearing down and re-creating.

2. Mock the SLM client so integration tests don't need Ollama.

Tests use fixtures (test_pg_conn, test_neo4j_session) that connect to
the test plane on ports 5433 (postgres) and 7688 (neo4j). The dev
plane on 5432/7687 is never touched.

Run with: uv run pytest -m integration
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import agentradar_store.slm as slm_module
import asyncpg
import pytest

from neo4j import AsyncGraphDatabase

# ---- Test data plane connection settings -------------------------------


class TestConfig:
    """Single source of truth for test-plane connection details.
    Reads from env vars; defaults work inside the test-runner container."""

    POSTGRES_HOST: str = os.environ.get("TEST_POSTGRES_HOST", "postgres-test")
    POSTGRES_PORT: int = int(os.environ.get("TEST_POSTGRES_PORT", "5432"))
    POSTGRES_USER: str = os.environ.get("TEST_POSTGRES_USER", "agentradar")
    POSTGRES_PASSWORD: str = os.environ.get("TEST_POSTGRES_PASSWORD", "agentradar_dev")
    POSTGRES_DB: str = os.environ.get("TEST_POSTGRES_DB", "agentradar")

    NEO4J_URI: str = os.environ.get("TEST_NEO4J_URI", "bolt://neo4j-test:7687")
    NEO4J_USER: str = os.environ.get("TEST_NEO4J_USER", "neo4j")
    NEO4J_PASSWORD: str = os.environ.get("TEST_NEO4J_PASSWORD", "agentradar_dev")

    MINIO_ENDPOINT: str = os.environ.get("TEST_MINIO_ENDPOINT", "http://minio-test:9000")
    MINIO_ACCESS_KEY: str = os.environ.get("TEST_MINIO_ACCESS_KEY", "agentradar")
    MINIO_SECRET_KEY: str = os.environ.get("TEST_MINIO_SECRET_KEY", "agentradar_dev")
    MINIO_BUCKET: str = os.environ.get("TEST_MINIO_BUCKET", "agentradar-artifacts")

    MCP_URL: str = os.environ.get("TEST_MCP_URL", "http://api-test:8000/mcp/")


# ---- SLM mock (unchanged) ---------------------------------------------


class StubSLMClient:
    def __init__(self) -> None:
        self._defaults: dict[str, dict[str, Any]] = {
            "forecast": {
                "prediction": "Trajectory will continue, with moderate uptake.",
                "confidence": 0.5,
                "horizon_months": 6,
                "reasoning": "Stub response; not a real prediction.",
                "cited_concept_ids": [],
            },
            "synthesis": {
                "themes": "Stub themes for integration test.",
                "standout": "Stub standout for integration test.",
            },
        }
        self._queues: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.calls: list[dict[str, Any]] = []

    def queue(self, intent: str, response: dict[str, Any]) -> None:
        self._queues[intent].append(response)

    def set_default(self, intent: str, response: dict[str, Any]) -> None:
        self._defaults[intent] = response

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> str:
        intent = self._classify(response_format)
        self.calls.append({"intent": intent, "system": system[:120]})
        queue = self._queues.get(intent, [])
        payload = queue.pop(0) if queue else self._defaults.get(intent, {})
        return json.dumps(payload)

    @staticmethod
    def _classify(response_format: dict | None) -> str:
        if not response_format:
            return "other"
        props = response_format.get("properties", {})
        if "themes" in props and "standout" in props:
            return "synthesis"
        if "prediction" in props and "confidence" in props:
            return "forecast"
        return "other"

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def stub_slm(monkeypatch):
    stub = StubSLMClient()
    monkeypatch.setattr(slm_module, "_singleton", stub)
    yield stub
    monkeypatch.setattr(slm_module, "_singleton", None)


# ---- Per-test data fixtures (connect to the test plane) ---------------


@pytest.fixture
async def test_pg_conn() -> AsyncIterator[asyncpg.Connection]:
    """Connection to postgres-test."""
    conn = await asyncpg.connect(
        host=TestConfig.POSTGRES_HOST,
        port=TestConfig.POSTGRES_PORT,
        user=TestConfig.POSTGRES_USER,
        password=TestConfig.POSTGRES_PASSWORD,
        database=TestConfig.POSTGRES_DB,
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def test_neo4j_session():
    """Session against neo4j-test. Wipes Neo4j before and after each test."""
    driver = AsyncGraphDatabase.driver(
        TestConfig.NEO4J_URI,
        auth=(TestConfig.NEO4J_USER, TestConfig.NEO4J_PASSWORD),
    )
    try:
        async with driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
            yield session
            await session.run("MATCH (n) DETACH DELETE n")
    finally:
        await driver.close()


@pytest.fixture
async def test_pg_clean(test_pg_conn):
    """Wipe test Postgres tables before each test. Use when a test
    needs a clean slate; skip when seeding via narrower fixtures."""
    await test_pg_conn.execute("TRUNCATE digests CASCADE")
    await test_pg_conn.execute("TRUNCATE forecasts CASCADE")
    await test_pg_conn.execute("TRUNCATE mention_events CASCADE")
    await test_pg_conn.execute("TRUNCATE pending_triples CASCADE")
    await test_pg_conn.execute("TRUNCATE concept_embeddings CASCADE")
    yield test_pg_conn
