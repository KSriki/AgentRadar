"""Pure-logic unit tests for agentradar_store helpers — no network."""

from __future__ import annotations

from agentradar_store.pg_client import PgClient, _slope, _vec


class TestSlope:
    def test_empty_returns_zero(self) -> None:
        assert _slope([]) == 0.0

    def test_single_point_returns_zero(self) -> None:
        assert _slope([5]) == 0.0

    def test_flat_returns_zero(self) -> None:
        assert _slope([3, 3, 3, 3]) == 0.0

    def test_increasing_is_positive(self) -> None:
        assert _slope([1, 2, 3, 4, 5]) > 0

    def test_decreasing_is_negative(self) -> None:
        assert _slope([5, 4, 3, 2, 1]) < 0

    def test_perfect_linear_slope(self) -> None:
        # y = 2x: slope should be exactly 2.0
        assert _slope([0, 2, 4, 6, 8]) == 2.0


class TestVecFormatter:
    def test_formats_simple_vector(self) -> None:
        assert _vec([1.0, 2.0, 3.0]) == "[1.0,2.0,3.0]"

    def test_handles_empty(self) -> None:
        assert _vec([]) == "[]"

    def test_no_spaces(self) -> None:
        # pgvector parser tolerates spaces but the format is tighter without.
        result = _vec([0.1, 0.2, 0.3])
        assert " " not in result


class TestTripleHash:
    def test_deterministic(self) -> None:
        h1 = PgClient.hash_triple("MCP", "INTRODUCED_BY", "Anthropic", "src1")
        h2 = PgClient.hash_triple("MCP", "INTRODUCED_BY", "Anthropic", "src1")
        assert h1 == h2

    def test_differs_on_any_field_change(self) -> None:
        base = PgClient.hash_triple("A", "P", "B", "s")
        assert base != PgClient.hash_triple("A2", "P", "B", "s")
        assert base != PgClient.hash_triple("A", "P2", "B", "s")
        assert base != PgClient.hash_triple("A", "P", "B2", "s")
        assert base != PgClient.hash_triple("A", "P", "B", "s2")

    def test_hex_format(self) -> None:
        h = PgClient.hash_triple("a", "b", "c", "d")
        assert len(h) == 64  # sha256 hex
        assert all(c in "0123456789abcdef" for c in h)