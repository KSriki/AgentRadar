"""
SLM (small language model) client. Pluggable provider: Ollama (local) or
Bedrock (cloud). Selected via SLM_PROVIDER env var.

The Protocol defines a minimal interface — just enough for extraction-style
tasks where we send a system prompt + user message and want back JSON or
short text. Anything more elaborate (tool use, streaming, multi-turn)
belongs in the agent code, not here.
"""

from __future__ import annotations

import json
from typing import Protocol

import httpx

from agentradar_core import SLMSettings, get_logger, settings

log = get_logger(__name__)


class SLMClient(Protocol):
    """Minimal interface for narrow extraction/classification tasks."""

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Return the model's text response. No streaming, no tool use."""
        ...


# ---------------------------------------------------------------------------
# Ollama implementation
# ---------------------------------------------------------------------------


class OllamaClient:
    """
    Talks to a local Ollama server over HTTP. Reuses one httpx.AsyncClient
    for connection pooling.
    """

    def __init__(self, cfg: SLMSettings) -> None:
        self._cfg = cfg
        # Long timeout — first call after model load can take 30s+ on CPU
        self._http = httpx.AsyncClient(
            base_url=cfg.ollama_base_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        payload = {
            "model": self._cfg.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "num_predict": max_tokens or self._cfg.max_tokens,
                "temperature": temperature
                if temperature is not None
                else self._cfg.temperature,
            },
        }
        resp = await self._http.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        content: str = data["message"]["content"]
        return content.strip()

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Bedrock implementation (for when AWS is wired up)
# ---------------------------------------------------------------------------


class BedrockClient:
    """Talks to Claude via AWS Bedrock. Same interface as OllamaClient."""

    def __init__(self, cfg: SLMSettings, region: str) -> None:
        self._cfg = cfg
        self._region = region

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        # Local import so we don't pay aioboto3's import cost when using Ollama
        import aioboto3

        session = aioboto3.Session(region_name=self._region)
        async with session.client("bedrock-runtime") as br:
            resp = await br.invoke_model(
                modelId=self._cfg.bedrock_model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens or self._cfg.max_tokens,
                    "temperature": temperature
                    if temperature is not None
                    else self._cfg.temperature,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }),
            )
            payload = json.loads(await resp["body"].read())
        text: str = payload["content"][0]["text"]
        return text.strip()

    async def close(self) -> None:
        # aioboto3 session is per-call; nothing to clean up
        pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_singleton: SLMClient | None = None


def get_slm_client() -> SLMClient:
    """Return the process-wide SLM client based on SLM_PROVIDER."""
    global _singleton
    if _singleton is None:
        if settings.slm.provider == "ollama":
            _singleton = OllamaClient(settings.slm)
            log.info(
                "slm.client_initialized",
                provider="ollama",
                model=settings.slm.ollama_model,
                url=settings.slm.ollama_base_url,
            )
        elif settings.slm.provider == "bedrock":
            _singleton = BedrockClient(settings.slm, settings.bedrock.aws_region)
            log.info(
                "slm.client_initialized",
                provider="bedrock",
                model=settings.slm.bedrock_model_id,
            )
        else:
            raise NotImplementedError(
                f"SLM provider {settings.slm.provider!r} not supported"
            )
    return _singleton