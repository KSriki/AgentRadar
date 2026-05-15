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

from fastmcp import Client

from agentradar_core import get_logger
from agentradar_store import get_pg_client
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
        concept = self._forced_concept or await self._select_candidate()
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

        result = await mcp.call_tool("propose_forecast", {
            "concept_name": forecast["concept_name"],
            "claim": forecast["prediction"],
            "confidence": forecast["confidence"],
            "confidence_band": band,
            "horizon_months": forecast.get("horizon_months", 6),   # default 6 months
            "reasoning": forecast.get("reasoning", ""),
            "cited_source_ids": forecast.get("cited_concept_ids", []),
            "evidence_snapshot": forecast.get("evidence_snapshot", {}),
        })
        log.info("forecaster.run.persisted", **result.data)

        return {
            "forecasts_produced": 1,
            "concept": concept,
            "confidence_band": band,
            "confidence": forecast["confidence"],
        }

    async def _select_candidate(self) -> str | None:
        """
        Select the next concept to forecast.

        Heuristic for Session 1: highest mention velocity in the last 90 days
        among concepts that don't have a recent (last 14 days) forecast.
        """
        pg = get_pg_client()
        pool = await pg._ensure()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                WITH recent_forecasts AS (
                    SELECT DISTINCT concept_name
                    FROM forecasts
                    WHERE generated_at > NOW() - INTERVAL '14 days'
                ),
                concept_volume AS (
                    SELECT concept_name, COUNT(*)::int AS n
                    FROM mention_events
                    WHERE observed_at > NOW() - INTERVAL '90 days'
                    GROUP BY concept_name
                )
                SELECT concept_name
                FROM concept_volume
                WHERE concept_name NOT IN (SELECT concept_name FROM recent_forecasts)
                ORDER BY n DESC
                LIMIT 1
                """,
            )
        return row["concept_name"] if row else None