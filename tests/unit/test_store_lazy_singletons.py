"""
Verify the lazy-singleton pattern: importing the store should NOT make
network calls, and repeated calls to get_*_client should return the same instance.
"""

from __future__ import annotations

from agentradar_store import (
    get_neo4j_client,
    get_pg_client,
    get_s3_client,
)


class TestLazySingletons:
    def test_neo4j_singleton(self) -> None:
        a = get_neo4j_client()
        b = get_neo4j_client()
        assert a is b

    def test_pg_singleton(self) -> None:
        a = get_pg_client()
        b = get_pg_client()
        assert a is b

    def test_s3_singleton(self) -> None:
        a = get_s3_client()
        b = get_s3_client()
        assert a is b

    def test_neo4j_does_not_connect_on_construction(self) -> None:
        """The driver is only created when .connect() or a session is used."""
        client = get_neo4j_client()
        assert client._driver is None  # noqa: SLF001 — testing internal invariant

    def test_pg_does_not_connect_on_construction(self) -> None:
        client = get_pg_client()
        assert client._pool is None  # noqa: SLF001