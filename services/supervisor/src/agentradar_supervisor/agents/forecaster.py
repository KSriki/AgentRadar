"""
Forecaster agent — the first ROMA consumer.

Implements the Agent Protocol. Its run() method:
  1. Selects which concept(s) to forecast this invocation
  2. Builds a ForecastTask for each
  3. Invokes the ROMA graph for each task
  4. Persists each final_forecast via MCP

Session 1: one concept per run, selected as "the concept with the highest
mention velocity that doesn't yet have a fresh forecast." This is a
deliberately narrow selection — broader candidate selection logic
(top_n_concepts) is Session 2 work.
"""

from __future__ import annotations

import time
from typing import Any

from agentradar_core import get_logger
from fastmcp import Client

from agentradar_supervisor.graph import get_roma_graph
from agentradar_supervisor.state import ForecastState, ForecastTask

log = get_logger(__name__)


class Forecaster:
    """Atomic-task Forecaster: forecasts one concept per invocation."""

    name = "forecaster"

    def __init__(self, concept_name: str | None = None) -> None:
        """
        Args:
            concept_name: If provided, force-forecast this specific concept
                          (useful for demos and tests). If None, select the
                          highest-velocity concept automatically.
        """
        self._forced_concept = concept_name

    async def run(self, mcp: Client) -> dict[str, Any]:
        log.info("forecaster.run.start", forced_concept=self._forced_concept)

        # ---- Step 1: pick a concept to forecast ----
        # Step 1: pick a concept to forecast
        concept = self._forced_concept or await self._select_candidate(mcp)
        if concept is None:
            log.info("forecaster.run.no_candidates")
            return {"forecasts_produced": 0}

        # ---- Step 2: build the task ----
        task: ForecastTask = {
            "kind": "forecast.concept",
            "concept_name": concept,
        }
        initial_state: ForecastState = {
            "task": task,
            "depth": 0,
            "trace_id": f"forecast-{concept}-{int(time.time())}",
            "parent_context": {},
            "subtask_results": [],
            "mcp": mcp,
        }

        # ---- Step 3: invoke ROMA ----
        graph = get_roma_graph()
        final_state = await graph.ainvoke(initial_state)

        # ---- Step 4: persist via MCP ----
        forecast = final_state.get("final_forecast", {})
        band = final_state.get("confidence_band", "weak")
        if not forecast:
            log.warning("forecaster.run.no_forecast_produced", concept=concept)
            return {"forecasts_produced": 0}

        result = await mcp.call_tool(
            "propose_forecast",
            {
                "concept_name": forecast["concept_name"],
                "claim": forecast["prediction"],
                "confidence": forecast["confidence"],
                "confidence_band": band,
                "horizon_months": forecast.get("horizon_months", 6),  # default 6 months
                "reasoning": forecast.get("reasoning", ""),
                "cited_source_ids": forecast.get("cited_concept_ids", []),
                "evidence_snapshot": forecast.get("evidence_snapshot", {}),
            },
        )
        log.info("forecaster.run.persisted", **result.data)

        return {
            "forecasts_produced": 1,
            "concept": concept,
            "confidence_band": band,
            "confidence": forecast["confidence"],
        }

    async def _select_candidate(self, mcp: Client) -> str | None:
        """
        Select the next concept to forecast via MCP.

        Heuristic: highest mention velocity in the last 90 days among
        concepts that haven't been forecasted in the last 14 days.
        """
        result = await mcp.call_tool(
            "select_forecast_candidate",
            {
                "velocity_window_days": 90,
                "cooldown_days": 14,
            },
        )
        return result.data.get("concept_name")

    async def run_topn(self, mcp: Client, top_n: int = 5) -> dict[str, Any]:
        """
        Execute a forecast.top_n composite workflow.

        Runs the Planner→Executor→Aggregator recursion through the same
        ROMA graph the atomic Forecaster uses. After this returns, N
        forecast rows have been persisted (one per top-N concept).
        """
        log.info("forecaster.run_topn.start", top_n=top_n)

        task: ForecastTask = {"kind": "forecast.top_n", "top_n": top_n}
        initial_state: ForecastState = {
            "task": task,
            "depth": 0,
            "trace_id": f"topn-{int(time.time())}",
            "parent_context": {},
            "mcp": mcp,
            "subtask_results": [],
        }
        graph = get_roma_graph()
        final_state = await graph.ainvoke(initial_state)

        topn_forecasts = final_state.get("final_topn", [])

        # Each top-N forecast has already been persisted by the inner
        # forecast.concept recursion calling propose_forecast. We don't
        # double-persist them here.
        log.info("forecaster.run_topn.done", count=len(topn_forecasts))
        return {
            "forecasts_produced": len(topn_forecasts),
            "concepts": [f["concept_name"] for f in topn_forecasts],
        }

    async def run_digest(
        self, mcp: Client, top_n: int = 5, label: str | None = None
    ) -> dict[str, Any]:
        """Execute the forecast.digest composite workflow."""
        actual_label = label or f"Weekly digest, week of {time.strftime('%Y-%m-%d')}"
        log.info("forecaster.run_digest.start", label=actual_label, top_n=top_n)

        task: ForecastTask = {
            "kind": "forecast.digest",
            "top_n": top_n,
            "digest_label": actual_label,
        }
        initial_state: ForecastState = {
            "task": task,
            "depth": 0,
            "trace_id": f"digest-{int(time.time())}",
            "parent_context": {},
            "mcp": mcp,
            "subtask_results": [],
        }
        graph = get_roma_graph()
        final_state = await graph.ainvoke(initial_state)

        digest = final_state.get("final_digest", {})
        if not digest or not digest.get("forecasts"):
            log.warning("forecaster.run_digest.no_digest_produced")
            return {"digests_produced": 0}

        # Persist the digest itself (forecasts inside are already persisted)
        result = await mcp.call_tool(
            "propose_digest",
            {
                "label": digest["label"],
                "themes": digest["themes"],
                "standout": digest["standout"],
                "forecasts": digest["forecasts"],
                "average_confidence": digest["average_confidence"],
                "confidence_band": final_state.get("confidence_band", "weak"),
            },
        )
        log.info("forecaster.run_digest.persisted", **result.data)
        return {
            "digests_produced": 1,
            "digest_id": result.data.get("digest_id"),
            "forecasts_count": len(digest["forecasts"]),
            "band": final_state.get("confidence_band"),
        }
