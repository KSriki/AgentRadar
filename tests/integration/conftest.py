"""
Integration-test conftest. Mocks the SLM client so integration tests
can run in CI without Ollama or any other model server present.

The stub is autouse=True at session scope: every integration test
implicitly substitutes the stub for the real SLM client. Tests that
want to assert on SLM behavior configure the stub explicitly.

This matches the pattern in tests/conftest.py's MockMCPClient — mock
at the abstraction boundary, not at the network boundary. Doesn't
require a fake HTTP server, doesn't add a library dependency, and
runs in milliseconds.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import agentradar_store.slm as slm_module
import pytest


class StubSLMClient:
    """
    Stand-in for OllamaClient / BedrockClient in integration tests.

    Queue responses keyed by a coarse "intent" (forecast | synthesis | other)
    OR set a default JSON dict that every call returns. The Forecaster only
    cares about getting valid JSON back, so by default we hand it a
    confidence-0.5 generic forecast that exercises happy-path execution
    without claiming specific predictions.
    """

    def __init__(self) -> None:
        self._defaults: dict[str, dict[str, Any]] = {
            "forecast": {
                "prediction": "Trajectory will continue, with moderate uptake.",
                "confidence": 0.5,
                "horizon_months": 6,
                "reasoning": "Stub response; not a real prediction.",
                "cited_concept_ids": [],
            },
            "synthesis": {
                "themes": "Stub themes for integration test.",
                "standout": "Stub standout for integration test.",
            },
        }
        self._queues: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.calls: list[dict[str, Any]] = []

    def queue(self, intent: str, response: dict[str, Any]) -> None:
        """Push a one-time response for a specific intent."""
        self._queues[intent].append(response)

    def set_default(self, intent: str, response: dict[str, Any]) -> None:
        """Permanently change the default response for an intent."""
        self._defaults[intent] = response

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> str:
        # Classify the call by what fields the schema expects.
        # The Forecaster's atomic prompt expects "prediction" + "confidence";
        # the digest's synthesis prompt expects "themes" + "standout".
        intent = self._classify(response_format)
        self.calls.append({"intent": intent, "system": system[:120]})

        queue = self._queues.get(intent, [])
        payload = queue.pop(0) if queue else self._defaults.get(intent, {})

        return json.dumps(payload)

    @staticmethod
    def _classify(response_format: dict | None) -> str:
        if not response_format:
            return "other"
        props = response_format.get("properties", {})
        if "themes" in props and "standout" in props:
            return "synthesis"
        if "prediction" in props and "confidence" in props:
            return "forecast"
        return "other"

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def stub_slm(monkeypatch):
    """
    Auto-substitute StubSLMClient for the real SLM client across every
    integration test. The substitution is at the module level so that
    `get_slm_client()` returns the stub whether called from agent code
    or from inside the api process (since both share the same singleton).
    """
    stub = StubSLMClient()

    # Reset the slm module's singleton so the next get_slm_client() call
    # returns OUR stub, not whatever was cached from a prior test run.
    monkeypatch.setattr(slm_module, "_singleton", stub)

    yield stub

    # Cleanup: clear the singleton so other tests / processes don't see
    # the stub leaking past the test scope.
    monkeypatch.setattr(slm_module, "_singleton", None)
