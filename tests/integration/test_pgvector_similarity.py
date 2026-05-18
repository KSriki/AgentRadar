"""
Integration tests for pgvector cosine-similarity search with HNSW.

Validates HNSW returns expected nearest neighbors on small fixtures.
ivfflat silently returns wrong results on tiny tables; HNSW does not.
These tests pin that property.
"""

from __future__ import annotations

import math

import pytest


@pytest.mark.integration
class TestPgVectorSimilarity:
    """HNSW returns sensible nearest neighbors on small fixtures."""

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0:
            return vec
        return [x / norm for x in vec]

    @pytest.mark.asyncio
    async def test_nearest_neighbor_recovers_inserted_vector(self, clean_pg):
        DIM = 1024

        def vec(seed: int) -> list[float]:
            base = [0.0] * DIM
            base[seed] = 1.0
            return base

        await clean_pg.upsert_embedding("Alpha", vec(0))
        await clean_pg.upsert_embedding("Beta", vec(1))
        await clean_pg.upsert_embedding("Gamma", vec(2))

        results = await clean_pg.search_similar_concepts(vec(1), limit=3)
        assert len(results) >= 1
        names = [r["concept_name"] for r in results]
        assert names[0] == "Beta"

    @pytest.mark.asyncio
    async def test_far_vector_returns_results_in_distance_order(self, clean_pg):
        DIM = 1024

        def vec(seed: int) -> list[float]:
            base = [0.0] * DIM
            base[seed] = 1.0
            return base

        await clean_pg.upsert_embedding("Near", vec(0))
        await clean_pg.upsert_embedding("Far", vec(500))

        query = vec(0)
        query[1] = 0.01
        query = self._normalize(query)

        results = await clean_pg.search_similar_concepts(query, limit=2)
        assert len(results) == 2
        assert results[0]["concept_name"] == "Near"
        assert results[1]["concept_name"] == "Far"

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_embedding(self, clean_pg):
        DIM = 1024
        v1 = [0.0] * DIM
        v1[0] = 1.0
        v2 = [0.0] * DIM
        v2[1] = 1.0

        await clean_pg.upsert_embedding("Same", v1)
        await clean_pg.upsert_embedding("Same", v2)

        results = await clean_pg.search_similar_concepts(v2, limit=5)
        same_results = [r for r in results if r["concept_name"] == "Same"]
        assert len(same_results) == 1

    @pytest.mark.asyncio
    async def test_hnsw_index_exists(self, clean_pg):
        """Pin the architectural choice: index is HNSW, not ivfflat."""
        pool = await clean_pg._ensure()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'concept_embeddings'
                """
            )
        index_defs = "\n".join(r["indexdef"] for r in rows)
        assert "hnsw" in index_defs.lower(), (
            f"Expected HNSW index on concept_embeddings, got: {index_defs}"
        )
        assert "ivfflat" not in index_defs.lower(), (
            f"Found ivfflat index — should be HNSW only: {index_defs}"
        )
