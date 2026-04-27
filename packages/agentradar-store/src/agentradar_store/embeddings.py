"""
Embedding client. Currently wraps Bedrock Titan; pluggable for local
(Ollama) later via the EMBEDDING_PROVIDER setting.

We isolate this behind a tiny interface (`embed(texts) -> list[list[float]]`)
so the rest of the system never imports a specific embedding SDK.
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol

import aioboto3

from agentradar_core import EmbeddingSettings, get_logger, settings

log = get_logger(__name__)


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_one(self, text: str) -> list[float]: ...


class BedrockTitanEmbeddings:
    """Amazon Titan Text Embeddings v2 via Bedrock."""

    def __init__(self, cfg: EmbeddingSettings, region: str) -> None:
        self._cfg = cfg
        self._session = aioboto3.Session(region_name=region)

    async def embed_one(self, text: str) -> list[float]:
        async with self._session.client("bedrock-runtime") as br:
            resp = await br.invoke_model(
                modelId=self._cfg.model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(
                    {"inputText": text, "dimensions": self._cfg.dim}
                ),
            )
            payload = json.loads(await resp["body"].read())
        embedding: list[float] = payload["embedding"]
        if len(embedding) != self._cfg.dim:
            raise ValueError(
                f"Embedding dim mismatch: got {len(embedding)}, "
                f"expected {self._cfg.dim}"
            )
        return embedding

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Titan doesn't batch; parallelize with bounded concurrency.
        sem = asyncio.Semaphore(8)

        async def _one(t: str) -> list[float]:
            async with sem:
                return await self.embed_one(t)

        return await asyncio.gather(*(_one(t) for t in texts))


_singleton: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    global _singleton
    if _singleton is None:
        if settings.embedding.provider == "bedrock":
            _singleton = BedrockTitanEmbeddings(
                settings.embedding, settings.bedrock.aws_region
            )
        else:
            raise NotImplementedError(
                f"Embedding provider '{settings.embedding.provider}' not yet supported"
            )
    return _singleton