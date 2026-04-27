"""
Async Neo4j client wrapper.

Design:
- One AsyncDriver per process (Neo4j drivers manage their own pool internally)
- Sessions are short-lived; created per logical unit of work
- High-level helpers (commit_triple_relationship, fetch_concept) live here so
  callers never write raw Cypher for routine operations
- Raw Cypher escape hatch via .session() for the Forecaster's complex traversals
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from agentradar_core import Neo4jSettings, get_logger, settings

log = get_logger(__name__)


class Neo4jClient:
    """Thin async wrapper around the official Neo4j driver."""

    def __init__(self, cfg: Neo4jSettings) -> None:
        self._cfg = cfg
        self._driver: AsyncDriver | None = None

    async def connect(self) -> None:
        """Create the driver. Idempotent."""
        if self._driver is not None:
            return
        self._driver = AsyncGraphDatabase.driver(
            self._cfg.uri,
            auth=(self._cfg.user, self._cfg.password.get_secret_value()),
        )
        # verify_connectivity raises on bad creds / unreachable host — fail fast
        await self._driver.verify_connectivity()
        log.info("neo4j.connected", uri=self._cfg.uri)

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            log.info("neo4j.closed")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Yield a Neo4j session. Use for arbitrary Cypher.

        Example:
            async with client.session() as s:
                result = await s.run("MATCH (c:Concept) RETURN count(c) AS n")
                row = await result.single()
        """
        if self._driver is None:
            await self.connect()
        assert self._driver is not None
        async with self._driver.session() as s:
            yield s

    # ----- high-level helpers ---------------------------------------------

    async def commit_triple_relationship(
        self,
        subject: str,
        predicate: str,
        object_: str,
        source_id: str,
        confidence: float,
    ) -> None:
        """
        MERGE the two Concept nodes and the Source node, then create a typed
        relationship between them with provenance properties.

        Predicate becomes the relationship type, so it must be a valid Neo4j
        identifier (validated by the Critic before this is ever called).
        """
        # Note: we use apoc.create.relationship to allow dynamic relationship types.
        # Standard Cypher CREATE requires the relationship type be a literal.
        cypher = """
        MERGE (subj:Concept {name: $subject})
        MERGE (obj:Concept {name: $object})
        MERGE (src:Source {id: $source_id})
        WITH subj, obj, src
        CALL apoc.create.relationship(
            subj, $predicate,
            {
                confidence: $confidence,
                source_id: $source_id,
                observed_at: datetime()
            },
            obj
        ) YIELD rel
        RETURN rel
        """
        async with self.session() as s:
            await s.run(
                cypher,
                subject=subject,
                object=object_,
                predicate=predicate,
                source_id=source_id,
                confidence=confidence,
            )
        log.info(
            "neo4j.triple_committed",
            subject=subject,
            predicate=predicate,
            object=object_,
            source_id=source_id,
        )

    async def fetch_concept(self, name: str) -> dict[str, Any] | None:
        """Return the Concept node + its first-degree edges, or None."""
        cypher = """
        MATCH (c:Concept {name: $name})
        OPTIONAL MATCH (c)-[r]-(other)
        RETURN c,
               collect({
                   type: type(r),
                   props: properties(r),
                   other: properties(other)
               }) AS edges
        """
        async with self.session() as s:
            result = await s.run(cypher, name=name)
            row = await result.single()
        if row is None:
            return None
        return {"concept": dict(row["c"]), "edges": row["edges"]}

    async def healthcheck(self) -> bool:
        """Return True if the driver can round-trip a trivial query."""
        try:
            async with self.session() as s:
                result = await s.run("RETURN 1 AS ok")
                row = await result.single()
            return row is not None and row["ok"] == 1
        except Exception as exc:
            log.warning("neo4j.healthcheck_failed", error=str(exc))
            return False


# ---- module-level singleton -----------------------------------------------

_singleton: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient:
    """Return the process-wide Neo4j client. Lazy: cheap to import."""
    global _singleton
    if _singleton is None:
        _singleton = Neo4jClient(settings.neo4j)
    return _singleton