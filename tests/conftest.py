"""
Shared pytest configuration and fixtures for the whole AgentRadar workspace.

Custom marks:
    @pytest.mark.integration    requires the docker-compose data plane up
    @pytest.mark.aws            requires real AWS Bedrock credentials

By default, integration and aws tests are skipped. Enable them with:
    uv run pytest -m integration
    uv run pytest -m "integration or aws"
    uv run pytest -m ""              # run everything regardless of marks
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest


# ---- mark registration -----------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires docker compose data plane (neo4j, postgres, minio)",
    )
    config.addinivalue_line(
        "markers",
        "aws: requires real AWS Bedrock credentials and network access",
    )


# ---- default mark filtering ------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """
    By default, skip integration + aws tests. To run them, pass an explicit
    -m selector on the command line (which makes config.getoption('markexpr')
    non-empty).
    """
    if config.getoption("markexpr"):
        return  # user asked for something specific; respect their choice
    skip_integration = pytest.mark.skip(
        reason="integration test (run with: uv run pytest -m integration)"
    )
    skip_aws = pytest.mark.skip(
        reason="aws test (run with: uv run pytest -m aws)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
        if "aws" in item.keywords:
            item.add_marker(skip_aws)


# ---- session-scoped event loop ---------------------------------------------
# pytest-asyncio's default is function-scoped, which means a new event loop per
# test. That breaks our async singleton clients (a connection created in test A
# is unusable in test B). Session scope reuses one loop for the whole run.


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    return asyncio.DefaultEventLoopPolicy()


# ---- env isolation for unit tests ------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """
    Strip every AGENTRADAR-relevant env var so a Settings instance built inside
    the test only sees explicit values, not whatever happens to be in your shell.
    """
    for key in [
        "ENVIRONMENT", "LOG_LEVEL",
        "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
        "POSTGRES_DSN",
        "S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_REGION",
        "AWS_REGION", "BEDROCK_MODEL_ID", "BEDROCK_CRITIC_MODEL_ID",
        "EMBEDDING_PROVIDER", "EMBEDDING_MODEL_ID", "EMBEDDING_DIM",
    ]:
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch


# ---- integration fixtures (only used by integration tests) -----------------


@pytest.fixture
async def neo4j_client() -> AsyncIterator:
    from agentradar_store import get_neo4j_client
    client = get_neo4j_client()
    await client.connect()
    yield client
    await client.close()
    # Reset the module-level singleton so the next test gets a fresh instance.
    import agentradar_store.neo4j_client as mod
    mod._singleton = None


@pytest.fixture
async def pg_client() -> AsyncIterator:
    from agentradar_store import get_pg_client
    client = get_pg_client()
    await client.connect()
    yield client
    await client.close()
    import agentradar_store.pg_client as mod
    mod._singleton = None


@pytest.fixture
async def s3_client() -> AsyncIterator:
    from agentradar_store import get_s3_client
    client = get_s3_client()
    yield client
    import agentradar_store.s3_client as mod
    mod._singleton = None


# ---- per-test data hygiene -------------------------------------------------


@pytest.fixture
async def clean_neo4j(neo4j_client) -> AsyncIterator:
    """Wipe the test Neo4j database before AND after each test using this fixture."""
    async def _wipe():
        async with neo4j_client.session() as s:
            await s.run("MATCH (n) DETACH DELETE n")
    await _wipe()
    yield neo4j_client
    await _wipe()


@pytest.fixture
async def clean_pg(pg_client) -> AsyncIterator:
    """Truncate test-relevant Postgres tables before AND after each test."""
    async def _wipe():
        pool = await pg_client._ensure()
        async with pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE pending_triples, mention_events, concept_embeddings "
                "RESTART IDENTITY"
            )
    await _wipe()
    yield pg_client
    await _wipe()