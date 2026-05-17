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
    config.addinivalue_line(
        "markers",
        "slow: end-to-end agent pipelines with real data plane + mocked externals",
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
    skip_slow = pytest.mark.skip(
        reason="slow test (run with: uv run pytest -m slow)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
        if "aws" in item.keywords:
            item.add_marker(skip_aws)
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


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


# ---- Mocked SLM client ----------------------------------------------------


class MockSLMClient:
    """
    Test double for the SLM client. Each test sets `responses` to a list of
    strings; calls return them in order. Records every call to `calls` for
    assertions.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses: list[str] = list(responses or [])
        self.calls: list[dict] = []

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        self.calls.append({
            "system": system, "user": user,
            "max_tokens": max_tokens, "temperature": temperature,
        })
        if not self.responses:
            raise RuntimeError("MockSLMClient: no more queued responses")
        return self.responses.pop(0)

    async def close(self) -> None:
        pass


@pytest.fixture
def mock_slm(monkeypatch: pytest.MonkeyPatch) -> MockSLMClient:
    """
    Replace the SLM singleton with a MockSLMClient. Patch every module
    that imports get_slm_client at the import site (where it's USED),
    not just at the source. This is how Python import semantics demand
    you patch — once a `from X import Y` happens, Y is rebound into
    the importing module's namespace.
    """
    mock = MockSLMClient()

    def _get_mock() -> MockSLMClient:
        return mock

    # Patch every module that does `from agentradar_store import get_slm_client`
    for module_path in [
        "agentradar_store.slm.get_slm_client",
        "agentradar_store.get_slm_client",
        "agentradar_supervisor.agents.critic.get_slm_client",
        "agentradar_supervisor.agents.scout.arxiv.get_slm_client",
        "agentradar_supervisor.agents.scout.tavily.get_slm_client",
        "agentradar_supervisor.agents.scout.trends.get_slm_client",
    ]:
        try:
            monkeypatch.setattr(module_path, _get_mock)
        except AttributeError:
            # Module may not be imported yet, or may not import this name
            pass
    return mock



# ---- Mocked Tavily client -------------------------------------------------


class MockTavilyClient:
    """Test double for TavilyResearchClient."""

    def __init__(self) -> None:
        self.search_responses: list[list] = []  # list of result-lists per call
        self.search_calls: list[dict] = []

    async def search(self, query: str, max_results: int | None = None,
                     search_depth: str | None = None) -> list:
        self.search_calls.append({
            "query": query, "max_results": max_results, "search_depth": search_depth,
        })
        if not self.search_responses:
            return []
        return self.search_responses.pop(0)

    async def healthcheck(self) -> bool:
        return True


@pytest.fixture
def mock_tavily(monkeypatch: pytest.MonkeyPatch) -> MockTavilyClient:
    mock = MockTavilyClient()

    def _get_mock() -> MockTavilyClient:
        return mock

    for module_path in [
        "agentradar_store.tavily.get_tavily_client",
        "agentradar_store.get_tavily_client",
        "agentradar_supervisor.agents.scout.tavily.get_tavily_client",
    ]:
        try:
            monkeypatch.setattr(module_path, _get_mock)
        except AttributeError:
            pass
    return mock


# ---- Mocked MCP client ----------------------------------------------------


class MockMCPClient:
    """
    Test double for fastmcp.Client. Tests pre-load responses keyed by
    tool name; calls record their args. Responses come back wrapped in
    a tiny shim that mimics fastmcp's `result.data` interface.
    """

    class _Result:
        def __init__(self, data) -> None:
            self.data = data

    def __init__(self) -> None:
        self.responses: dict[str, list] = {}  # tool_name -> [data, data, ...]
        self.calls: list[dict] = []
        self._tools_listed = False

    def queue(self, tool_name: str, data) -> None:
        self.responses.setdefault(tool_name, []).append(data)

    async def call_tool(self, name: str, args: dict) -> "MockMCPClient._Result":
        self.calls.append({"tool": name, "args": args})
        queue = self.responses.get(name, [])
        if not queue:
            # Sensible defaults for tools called incidentally by tests
            if name == "put_text_artifact":
                return self._Result({"key": args.get("key"), "uri": "mock://artifact"})
            if name == "record_mention":
                return self._Result({"recorded": True})
            if name == "propose_triple":
                return self._Result({"triple_id": "mock-id", "status": "pending"})
            if name in ("approve_triple", "reject_triple"):
                return self._Result({"committed": True, "decision": name.split("_")[0] + "d"})
            if name == "propose_forecast":                                              # <-- new
                return self._Result({"forecast_id": "mock-forecast-id", "status": "stored"})
            if name == "list_recent_forecasts":                                         # <-- new (bonus)
                return self._Result({"forecasts": [], "count": 0})
            if name == "propose_digest":
                return self._Result({"digest_id": "mock-digest-id", "status": "stored"})
            if name == "list_recent_digests":
                return self._Result({"digests": [], "count": 0})
            if name == "select_forecast_candidate":
                return self._Result({"concept_name": None})  # default to "no candidate"
            if name == "select_top_n_concepts":
                return self._Result({"concept_names": []})
            if name == "get_forecast_evidence":
                return self._Result({
                    "concept_name": "MockConcept",
                    "total_mentions": 0,
                    "source_diversity": 0,
                    "mentions_by_source": {},
                    "mention_velocity": {"velocity": 0.0, "buckets": []},
                })
            raise RuntimeError(f"MockMCPClient: no queued response for tool {name!r}")
        return self._Result(queue.pop(0))

    async def list_tools(self) -> list:
        self._tools_listed = True
        return []

    async def __aenter__(self) -> "MockMCPClient":
        return self

    async def __aexit__(self, *args) -> None:
        pass


@pytest.fixture
def mock_mcp() -> MockMCPClient:
    """Return a fresh MockMCPClient. Tests queue responses and inspect calls."""
    return MockMCPClient()


# ---- Temporary YAML fixtures ---------------------------------------------


@pytest.fixture
def tmp_yaml(tmp_path):
    """Helper: write a YAML dict to a tmp file and return the path."""
    import yaml as _yaml
    from pathlib import Path

    def _write(name: str, content: dict) -> Path:
        p = tmp_path / name
        p.write_text(_yaml.safe_dump(content))
        return p
    return _write


# ---- Reset query-planner / config caches ---------------------------------


@pytest.fixture(autouse=False)
def reset_settings_singletons(monkeypatch):
    """
    For tests that mutate env and need a fresh `settings` singleton.
    Not autouse — opt in only when needed (most unit tests don't care).
    """
    import agentradar_core.config as cfg_mod
    original = cfg_mod.settings
    yield
    cfg_mod.settings = original