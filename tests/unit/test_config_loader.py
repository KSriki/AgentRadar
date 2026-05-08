"""
Unit tests for the YAML config loader.

Tests cover:
  - Valid YAML produces the expected list of queries
  - Missing file raises FileNotFoundError immediately
  - Malformed YAML raises with a helpful error
  - YAML without the expected 'queries' key raises
  - Empty queries list raises (we want fail-fast on misconfiguration)
  - Whitespace-only entries are filtered out
  - load_tavily_queries combines static + derived correctly
  - When derivation fails, static queries still come back
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agentradar_supervisor.config_loader import (
    _load_static_queries,
    load_tavily_queries,
)


# ---- _load_static_queries (the YAML-specific layer) ----------------------


class TestLoadStaticQueries:
    """Pure-Python YAML parsing and validation."""

    def test_valid_yaml_returns_query_list(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("ok.yaml", {"queries": ["q1", "q2", "q3"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        out = _load_static_queries()
        assert out == ["q1", "q2", "q3"]

    def test_missing_file_raises_file_not_found(self, monkeypatch, tmp_path):
        nonexistent = str(tmp_path / "does_not_exist.yaml")
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            nonexistent,
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            _load_static_queries()

    def test_malformed_yaml_raises_value_error(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: valid: yaml: at: all: [unclosed\n")
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            str(bad),
        )
        with pytest.raises(ValueError, match="not valid YAML"):
            _load_static_queries()

    def test_missing_queries_key_raises(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("no_key.yaml", {"something_else": ["x"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        with pytest.raises(ValueError, match="must contain a top-level 'queries' list"):
            _load_static_queries()

    def test_empty_queries_list_raises(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("empty.yaml", {"queries": []})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        with pytest.raises(ValueError, match="non-empty list"):
            _load_static_queries()

    def test_non_list_queries_raises(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("notalist.yaml", {"queries": "single string not a list"})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        with pytest.raises(ValueError, match="non-empty list"):
            _load_static_queries()

    def test_whitespace_entries_filtered(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("mixed.yaml", {
            "queries": ["good", "  ", "", "also good", "   "],
        })
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        out = _load_static_queries()
        assert out == ["good", "also good"]

    def test_all_whitespace_raises(self, tmp_yaml, monkeypatch):
        """If filtering leaves zero usable entries, fail fast."""
        path = tmp_yaml("all_blank.yaml", {"queries": ["", "   ", "\t"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        with pytest.raises(ValueError, match="no usable queries"):
            _load_static_queries()

    def test_strips_surrounding_whitespace(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("strippable.yaml", {
            "queries": ["  q1  ", "\tq2\n", "q3"],
        })
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        assert _load_static_queries() == ["q1", "q2", "q3"]


# ---- load_tavily_queries (combines static + derived) ---------------------


class TestLoadTavilyQueries:
    """The async entry point that combines YAML + graph-derived queries."""

    @pytest.mark.asyncio
    async def test_combines_static_and_derived(self, tmp_yaml, monkeypatch):
        path = tmp_yaml("base.yaml", {"queries": ["static_a", "static_b"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        # Mock derive_tavily_queries to return derived ones
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.derive_tavily_queries",
            AsyncMock(return_value=["derived_x", "derived_y"]),
        )
        out = await load_tavily_queries()
        assert "static_a" in out
        assert "static_b" in out
        assert "derived_x" in out
        assert "derived_y" in out
        assert len(out) == 4

    @pytest.mark.asyncio
    async def test_static_takes_precedence_in_dedup(self, tmp_yaml, monkeypatch):
        """If derived produces a query that's also in static, it appears once."""
        path = tmp_yaml("base.yaml", {"queries": ["overlap", "static_only"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.derive_tavily_queries",
            AsyncMock(return_value=["overlap", "derived_only"]),
        )
        out = await load_tavily_queries()
        # 'overlap' should appear exactly once
        assert out.count("overlap") == 1
        assert "static_only" in out
        assert "derived_only" in out

    @pytest.mark.asyncio
    async def test_include_derived_false_returns_static_only(
        self, tmp_yaml, monkeypatch,
    ):
        path = tmp_yaml("base.yaml", {"queries": ["a", "b"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        # Set up a derive_tavily_queries that, if called, would taint output
        derive_mock = AsyncMock(return_value=["should_not_appear"])
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.derive_tavily_queries",
            derive_mock,
        )

        out = await load_tavily_queries(include_derived=False)
        assert out == ["a", "b"]
        # Verify derive was not called
        derive_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_derivation_failure_returns_static(self, tmp_yaml, monkeypatch):
        """If graph derivation explodes, we still want the static queries back."""
        path = tmp_yaml("base.yaml", {"queries": ["resilient"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.derive_tavily_queries",
            AsyncMock(side_effect=Exception("graph offline")),
        )
        out = await load_tavily_queries()
        assert out == ["resilient"]

    @pytest.mark.asyncio
    async def test_empty_derived_does_not_break_static(self, tmp_yaml, monkeypatch):
        """The cold-start case — empty graph yields no derived queries."""
        path = tmp_yaml("base.yaml", {"queries": ["coldstart_a", "coldstart_b"]})
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.settings.scout.tavily_queries_path",
            path,
        )
        monkeypatch.setattr(
            "agentradar_supervisor.config_loader.derive_tavily_queries",
            AsyncMock(return_value=[]),
        )
        out = await load_tavily_queries()
        assert out == ["coldstart_a", "coldstart_b"]