"""Integration tests against a real Neo4j (run with: uv run pytest -m integration)."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


class TestNeo4jHealthcheck:
    async def test_healthcheck_passes(self, neo4j_client) -> None:
        assert await neo4j_client.healthcheck() is True


class TestCommitTriple:
    async def test_commit_creates_concepts_and_relationship(
        self, clean_neo4j
    ) -> None:
        await clean_neo4j.commit_triple_relationship(
            subject="MCP",
            predicate="INTRODUCED_BY",
            object_="Anthropic",
            source_id="src-123",
            confidence=0.95,
        )

        async with clean_neo4j.session() as s:
            result = await s.run(
                """
                MATCH (subj:Concept {name: $subj})
                      -[r:INTRODUCED_BY]->
                      (obj:Concept {name: $obj})
                RETURN subj.name AS s, obj.name AS o,
                       r.confidence AS c, r.source_id AS src
                """,
                subj="MCP", obj="Anthropic",
            )
            row = await result.single()

        assert row is not None
        assert row["s"] == "MCP"
        assert row["o"] == "Anthropic"
        assert row["c"] == 0.95
        assert row["src"] == "src-123"

    async def test_fetch_concept_returns_edges(self, clean_neo4j) -> None:
        await clean_neo4j.commit_triple_relationship(
            "MCP", "INTRODUCED_BY", "Anthropic", "src-1", 0.9
        )
        await clean_neo4j.commit_triple_relationship(
            "MCP", "GOVERNED_BY", "LinuxFoundation", "src-2", 0.85
        )

        result = await clean_neo4j.fetch_concept("MCP")

        assert result is not None
        assert result["concept"]["name"] == "MCP"
        edge_types = {e["type"] for e in result["edges"]}
        assert {"INTRODUCED_BY", "GOVERNED_BY"} <= edge_types

    async def test_fetch_concept_returns_none_for_unknown(self, clean_neo4j) -> None:
        result = await clean_neo4j.fetch_concept("DefinitelyNotAConcept")
        assert result is None