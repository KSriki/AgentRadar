"""
Unit tests for the graph-aware query planner.

Tests cover:
  - Each strategy (corroboration, spike, adjacency) produces correctly
    templated queries from the right inputs
  - derive_tavily_queries dedupes, handles empty inputs, isolates per-strategy failures

External dependencies (Postgres, Neo4j) are mocked. The strategies are
pure templating once given structured input, so the tests exercise the
template logic against well-defined input shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentradar_supervisor.query_planner import (
    derive_tavily_queries,
    generate_adjacency_queries,
    generate_corroboration_queries,
    generate_spike_queries,
)


# ---- Helpers --------------------------------------------------------------


def _patch_pg(monkeypatch, *, singletons=None, spikes=None):
    """Replace get_pg_client with a mock returning the given fixture data."""
    mock_pg = MagicMock()
    mock_pg.find_singleton_concepts = AsyncMock(return_value=singletons or [])
    mock_pg.find_velocity_spikes = AsyncMock(return_value=spikes or [])

    def _factory():
        return mock_pg

    for path in [
        "agentradar_store.pg_client.get_pg_client",
        "agentradar_store.get_pg_client",
        "agentradar_supervisor.query_planner.get_pg_client",
    ]:
        try:
            monkeypatch.setattr(path, _factory)
        except AttributeError:
            pass
    return mock_pg


def _patch_neo4j(monkeypatch, *, authorities=None):
    mock_n = MagicMock()
    mock_n.list_top_authorities = AsyncMock(return_value=authorities or [])

    def _factory():
        return mock_n

    for path in [
        "agentradar_store.neo4j_client.get_neo4j_client",
        "agentradar_store.get_neo4j_client",
        "agentradar_supervisor.query_planner.get_neo4j_client",
    ]:
        try:
            monkeypatch.setattr(path, _factory)
        except AttributeError:
            pass
    return mock_n


# ---- Per-strategy tests ---------------------------------------------------


class TestCorroborationStrategy:
    """Singleton concepts → 'find a second source' queries."""

    @pytest.mark.asyncio
    async def test_empty_singletons_yields_no_queries(self, monkeypatch):
        _patch_pg(monkeypatch, singletons=[])
        queries = await generate_corroboration_queries(limit=5)
        assert queries == []

    @pytest.mark.asyncio
    async def test_each_singleton_becomes_one_query(self, monkeypatch):
        _patch_pg(monkeypatch, singletons=[
            {"concept": "Foo", "source_id": "tavily:abc",
             "observed_at": datetime.now(UTC)},
            {"concept": "Bar", "source_id": "arxiv:1234",
             "observed_at": datetime.now(UTC)},
        ])
        queries = await generate_corroboration_queries(limit=5)
        assert len(queries) == 2
        # Concept name should appear in the generated query
        assert any("Foo" in q for q in queries)
        assert any("Bar" in q for q in queries)

    @pytest.mark.asyncio
    async def test_query_includes_disambiguation_terms(self, monkeypatch):
        """A bare concept name like 'Apple' is ambiguous; the template should
        add agentic-AI context terms to disambiguate."""
        _patch_pg(monkeypatch, singletons=[
            {"concept": "MCP", "source_id": "tavily:x",
             "observed_at": datetime.now(UTC)},
        ])
        queries = await generate_corroboration_queries(limit=5)
        assert len(queries) == 1
        # The template adds agentic-AI context (framework/protocol/tool)
        assert any(
            term in queries[0].lower()
            for term in ("agent", "framework", "protocol", "tool")
        )


class TestSpikeStrategy:
    """Velocity-spiked concepts → 'find the announcement' queries."""

    @pytest.mark.asyncio
    async def test_empty_spikes_yields_no_queries(self, monkeypatch):
        _patch_pg(monkeypatch, spikes=[])
        queries = await generate_spike_queries(limit=5)
        assert queries == []

    @pytest.mark.asyncio
    async def test_each_spike_becomes_one_query(self, monkeypatch):
        _patch_pg(monkeypatch, spikes=[
            {"concept": "MCP", "recent_count": 8, "prior_count": 1},
            {"concept": "AutoGen", "recent_count": 5, "prior_count": 2},
        ])
        queries = await generate_spike_queries(limit=5)
        assert len(queries) == 2
        assert any("MCP" in q for q in queries)
        assert any("AutoGen" in q for q in queries)

    @pytest.mark.asyncio
    async def test_query_includes_announcement_keywords(self, monkeypatch):
        """Spike queries should look for launch/announcement signals."""
        _patch_pg(monkeypatch, spikes=[
            {"concept": "Foo", "recent_count": 6, "prior_count": 0},
        ])
        queries = await generate_spike_queries(limit=5)
        assert any(
            kw in queries[0].lower()
            for kw in ("announcement", "launch", "release")
        )


class TestAdjacencyStrategy:
    """Top authorities → 'what else have they announced?' queries."""

    @pytest.mark.asyncio
    async def test_empty_authorities_yields_no_queries(self, monkeypatch):
        _patch_neo4j(monkeypatch, authorities=[])
        queries = await generate_adjacency_queries(limit=5)
        assert queries == []

    @pytest.mark.asyncio
    async def test_each_authority_becomes_one_query(self, monkeypatch):
        _patch_neo4j(monkeypatch, authorities=[
            {"authority": "Anthropic", "concept_count": 5},
            {"authority": "Google", "concept_count": 3},
        ])
        queries = await generate_adjacency_queries(limit=5)
        assert len(queries) == 2
        assert any("Anthropic" in q for q in queries)
        assert any("Google" in q for q in queries)

    @pytest.mark.asyncio
    async def test_query_targets_new_things_from_authority(self, monkeypatch):
        """Adjacency template should ask for *new* things from the authority."""
        _patch_neo4j(monkeypatch, authorities=[
            {"authority": "OpenAI", "concept_count": 4},
        ])
        queries = await generate_adjacency_queries(limit=5)
        assert "OpenAI" in queries[0]
        # 'new' or 'recent' or similar — we want forward-looking signal
        assert any(
            kw in queries[0].lower()
            for kw in ("new", "framework", "tool", "protocol")
        )


# ---- derive_tavily_queries — orchestration --------------------------------


class TestDeriveTavilyQueries:
    """The top-level entry point that combines all three strategies."""

    @pytest.mark.asyncio
    async def test_empty_graph_yields_no_queries(self, monkeypatch):
        _patch_pg(monkeypatch)
        _patch_neo4j(monkeypatch)
        queries = await derive_tavily_queries()
        assert queries == []

    @pytest.mark.asyncio
    async def test_combines_all_three_strategies(self, monkeypatch):
        _patch_pg(
            monkeypatch,
            singletons=[
                {"concept": "Foo", "source_id": "x:1",
                 "observed_at": datetime.now(UTC)},
            ],
            spikes=[
                {"concept": "Bar", "recent_count": 5, "prior_count": 1},
            ],
        )
        _patch_neo4j(monkeypatch, authorities=[
            {"authority": "Baz", "concept_count": 3},
        ])
        queries = await derive_tavily_queries()
        assert len(queries) == 3
        # All three concepts/authorities should appear somewhere
        joined = " ".join(queries)
        assert "Foo" in joined
        assert "Bar" in joined
        assert "Baz" in joined

    @pytest.mark.asyncio
    async def test_dedupes_identical_queries(self, monkeypatch):
        """If two strategies produce the same query string, output has it once."""
        # Force collision: a singleton 'X' AND a spike on 'X' might template
        # the same way — depends on templates. Easier deterministic test:
        # ensure two singletons with same name produce only one query.
        _patch_pg(monkeypatch, singletons=[
            {"concept": "Foo", "source_id": "a:1", "observed_at": datetime.now(UTC)},
            {"concept": "Foo", "source_id": "b:2", "observed_at": datetime.now(UTC)},
        ])
        _patch_neo4j(monkeypatch)
        queries = await derive_tavily_queries()
        # Should have ≤1 unique 'Foo' query, not two duplicates
        foo_queries = [q for q in queries if "Foo" in q]
        assert len(foo_queries) == len(set(foo_queries))

    @pytest.mark.asyncio
    async def test_one_strategy_failure_does_not_break_others(self, monkeypatch):
        """If one strategy raises, others still contribute their queries."""
        # Set up Postgres mock that raises for spikes but works for singletons
        mock_pg = MagicMock()
        mock_pg.find_singleton_concepts = AsyncMock(return_value=[
            {"concept": "Survives", "source_id": "x:1",
             "observed_at": datetime.now(UTC)},
        ])
        mock_pg.find_velocity_spikes = AsyncMock(
            side_effect=Exception("spikes query exploded")
        )
        for path in [
            "agentradar_supervisor.query_planner.get_pg_client",
            "agentradar_store.get_pg_client",
        ]:
            try:
                monkeypatch.setattr(path, lambda: mock_pg)
            except AttributeError:
                pass
        _patch_neo4j(monkeypatch, authorities=[
            {"authority": "AlsoSurvives", "concept_count": 2},
        ])

        queries = await derive_tavily_queries()
        joined = " ".join(queries)
        assert "Survives" in joined
        assert "AlsoSurvives" in joined