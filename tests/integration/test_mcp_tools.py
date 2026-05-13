"""
Integration tests for the MCP tool layer via real HTTP transport.

These exercise the full ASGI stack: fastmcp + FastAPI + REST mount +
the underlying store clients. Catches a class of bugs unit tests miss:
serialization, async lifespan, transport-level edge cases.

Requires the api container running at http://localhost:8000.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastmcp import Client


MCP_URL = "http://localhost:8000/mcp/"


@pytest.fixture
async def mcp() -> Client:
    """Connected fastmcp client for tests. Cleanup on teardown."""
    async with Client(MCP_URL) as c:
        yield c


@pytest.mark.integration
class TestMcpHealthcheck:
    """Healthcheck tool returns expected shape with all backing services."""

    @pytest.mark.asyncio
    async def test_healthcheck_includes_all_stores(self, mcp):
        result = await mcp.call_tool("healthcheck", {})
        data = result.data
        assert "neo4j" in data
        assert "postgres" in data
        assert "s3" in data
        # All should be True if data plane is healthy
        assert all(isinstance(v, bool) for v in data.values())


@pytest.mark.integration
class TestMcpProposerCritic:
    """Propose → list → approve roundtrip via MCP HTTP."""

    @pytest.mark.asyncio
    async def test_propose_returns_pending_status(self, mcp, clean_pg, clean_neo4j):
        result = await mcp.call_tool("propose_triple", {
            "proposer_agent": "test", "subject": "MCP",
            "predicate": "INTRODUCED_BY", "object": "Anthropic",
            "source_id": "test:mcp1", "confidence": 0.8,
        })
        assert result.data["status"] == "pending"
        assert "triple_id" in result.data

    @pytest.mark.asyncio
    async def test_list_pending_returns_proposed(
        self, mcp, clean_pg, clean_neo4j,
    ):
        await mcp.call_tool("propose_triple", {
            "proposer_agent": "test", "subject": "X",
            "predicate": "MENTIONED_IN", "object": "src:a",
            "source_id": "src:a", "confidence": 0.5,
        })
        await mcp.call_tool("propose_triple", {
            "proposer_agent": "test", "subject": "Y",
            "predicate": "MENTIONED_IN", "object": "src:b",
            "source_id": "src:b", "confidence": 0.6,
        })

        result = await mcp.call_tool("list_pending_triples", {"limit": 10})
        assert len(result.data) == 2
        subjects = {t["subject"] for t in result.data}
        assert subjects == {"X", "Y"}

    @pytest.mark.asyncio
    async def test_approve_commits_to_neo4j(
        self, mcp, clean_pg, clean_neo4j,
    ):
        proposal = await mcp.call_tool("propose_triple", {
            "proposer_agent": "test", "subject": "MCP",
            "predicate": "INTRODUCED_BY", "object": "Anthropic",
            "source_id": "test:src1", "confidence": 0.95,
        })

        approve = await mcp.call_tool("approve_triple", {
            "triple_id": proposal.data["triple_id"],
        })
        assert approve.data["committed"] is True
        assert approve.data["decision"] == "approved"

        # Verify graph state via get_concept
        result = await mcp.call_tool("get_concept", {"name": "MCP"})
        assert result.data["found"] is True
        assert any(
            edge["type"] == "INTRODUCED_BY"
            and edge["other"]["name"] == "Anthropic"
            for edge in result.data["edges"]
        )

    @pytest.mark.asyncio
    async def test_reject_does_not_touch_neo4j(
        self, mcp, clean_pg, clean_neo4j,
    ):
        proposal = await mcp.call_tool("propose_triple", {
            "proposer_agent": "test", "subject": "Bogus",
            "predicate": "INTRODUCED_BY", "object": "NotReal",
            "source_id": "test:bogus", "confidence": 0.2,
        })

        reject = await mcp.call_tool("reject_triple", {
            "triple_id": proposal.data["triple_id"],
            "reason": "ontology violation",
        })
        assert reject.data["decision"] == "rejected"

        # Verify Bogus is NOT in graph
        result = await mcp.call_tool("get_concept", {"name": "Bogus"})
        assert result.data["found"] is False

@pytest.mark.integration
class TestMcpMentions:
    """record_mention + get_mention_velocity roundtrip."""

    @pytest.mark.asyncio
    async def test_record_mention_is_idempotent(self, mcp, clean_pg):
        for _ in range(3):
            await mcp.call_tool("record_mention", {
                "concept_name": "Test",
                "source_id": "src:1",
                "source_type": "arxiv",   # MCP serializes as string; server resolves to enum
                "observed_at": datetime.now(UTC).isoformat(),
            })
        result = await mcp.call_tool("get_mention_velocity", {
            "concept_name": "Test", "window_days": 7,
        })
        assert sum(b["mentions"] for b in result.data["buckets"]) == 1

    @pytest.mark.asyncio
    async def test_velocity_returns_buckets(self, mcp, clean_pg):
        for i in range(3):
            await mcp.call_tool("record_mention", {
                "concept_name": "Concept",
                "source_id": f"src:{i}",
                "source_type": "arxiv",
                "observed_at": datetime.now(UTC).isoformat(),
            })

        result = await mcp.call_tool("get_mention_velocity", {
            "concept_name": "Concept", "window_days": 30,
        })
        assert "buckets" in result.data
        assert "velocity" in result.data
        assert sum(b["mentions"] for b in result.data["buckets"]) == 3


@pytest.mark.integration
class TestMcpArtifacts:
    """put_text_artifact stores to S3 via MCP."""

    @pytest.mark.asyncio
    async def test_put_artifact_returns_uri(self, mcp, s3_client):
        result = await mcp.call_tool("put_text_artifact", {
            "key": "test/integration_test_artifact.json",
            "content": '{"test": "data"}',
            "content_type": "application/json",
        })
        assert "uri" in result.data

        # Verify retrievable
        body = await s3_client.get_artifact(
            "test/integration_test_artifact.json"
        )
        assert b'"test": "data"' in body


@pytest.mark.integration
class TestMcpTraversal:
    """traverse follows graph edges from a seed concept."""

    @pytest.mark.asyncio
    async def test_traverse_returns_neighbors(self, mcp, clean_pg, clean_neo4j):
        # Build the graph
        proposal = await mcp.call_tool("propose_triple", {
            "proposer_agent": "test",
            "subject": "MCP",
            "predicate": "INTRODUCED_BY",
            "object": "Anthropic",
            "source_id": "test:traverse",
            "confidence": 0.95,
        })
        await mcp.call_tool("approve_triple", {
            "triple_id": proposal.data["triple_id"],
        })

        result = await mcp.call_tool("traverse", {
            "start": "MCP",
            "edge_types": ["INTRODUCED_BY"],
            "depth": 2,
        })

        # Result is the structured envelope
        data = result.data
        assert data["start"] == "MCP"
        assert data["edge_types"] == ["INTRODUCED_BY"]
        assert isinstance(data["paths"], list)
        assert len(data["paths"]) >= 1

        # First path should include both concepts
        first_path = data["paths"][0]
        node_names = [n["name"] for n in first_path["nodes"]]
        assert "MCP" in node_names
        assert "Anthropic" in node_names

    @pytest.mark.asyncio
    async def test_traverse_empty_result_returns_envelope(self, mcp, clean_neo4j):
        """Traversing a node that doesn't exist returns empty paths, not error."""
        result = await mcp.call_tool("traverse", {
            "start": "NonexistentConcept",
            "edge_types": ["INTRODUCED_BY"],
            "depth": 2,
        })
        # Should succeed with empty paths
        assert result.data["paths"] == []
        assert result.data["start"] == "NonexistentConcept"