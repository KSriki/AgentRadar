"""
ROMA node implementations.

These are pure async functions over ForecastState. Each reads fields it
needs and returns a dict of fields to write. LangGraph composes them
into the graph via state-machine semantics.

Atomizer / Planner / Executor / Aggregator: the four ROMA roles. Per
agent (Forecaster, future agents) the executor differs; the other three
are mostly orchestration logic that doesn't change per workflow.

Per the SLM-only-where-needed principle:
- Atomizer: deterministic Python (no LLM)
- Planner: deterministic Python for known task kinds; LLM only for novel decomposition (Session 2+)
- Executor: LLM call (the actual reasoning step — earns its keep)
- Aggregator: deterministic Python (composes results; no model)
"""

from __future__ import annotations

import json
from typing import Any

from agentradar_core import get_logger
from agentradar_store import get_pg_client, get_slm_client
from agentradar_supervisor.state import (
    CandidateForecast,
    ForecastState,
    ForecastTask,
)

log = get_logger(__name__)


# Maximum ROMA recursion depth. Hard cap to prevent runaway recursion.
MAX_DEPTH = 3


# ---- Atomizer -------------------------------------------------------------


def atomize(state: ForecastState) -> dict[str, Any]:
    """
    Decide whether the task is atomic or composite.

    Atomicity rules (Session 1):
    - forecast.concept is atomic by definition (single concept)
    - Any task at MAX_DEPTH is atomic (force termination)
    - forecast.top_n and forecast.digest are composite (Session 2 territory)

    Returns the state delta — just is_atomic.
    """
    task = state["task"]
    kind = task.get("kind", "forecast.concept")
    depth = state.get("depth", 0)

    if depth >= MAX_DEPTH:
        log.warning("roma.atomize.depth_cap_reached", depth=depth, kind=kind)
        return {"is_atomic": True}

    if kind == "forecast.concept":
        return {"is_atomic": True}

    # forecast.top_n, forecast.digest, future kinds → composite
    return {"is_atomic": False}


# ---- Planner --------------------------------------------------------------


def plan(state: ForecastState) -> dict[str, Any]:
    """
    Decompose a composite task into subtasks.

    Session 1: this node exists structurally but should never run, since
    forecast.concept is atomic. The graph routes here only when is_atomic
    is False, which only happens for top_n/digest in Session 2+.

    Session 2: real decomposition logic per task kind.
    """
    task = state["task"]
    kind = task.get("kind")
    log.warning(
        "roma.plan.session2_not_implemented",
        kind=kind,
        message="Planner reached for a composite task in Session 1; should not happen.",
    )
    # Return empty subtasks; the graph short-circuits to aggregator with empty results
    return {"subtasks": []}


# ---- Executor (Forecaster-specific atomic execution) ---------------------


async def execute(state: ForecastState) -> dict[str, Any]:
    """
    Execute an atomic task. For Session 1 this means: do the actual
    forecasting for ONE concept.

    Sequence:
    1. Gather evidence from Postgres (mention history, velocity)
    2. Call the SLM with structured output to produce a candidate forecast
    3. Return both for the aggregator to finalize
    """
    task = state["task"]
    kind = task.get("kind")
    if kind != "forecast.concept":
        log.warning("roma.execute.unsupported_kind", kind=kind)
        return {"evidence": {}, "candidate_forecast": {}}

    concept_name = task.get("concept_name", "")
    if not concept_name:
        log.warning("roma.execute.no_concept_name")
        return {"evidence": {}, "candidate_forecast": {}}

    log.info("roma.execute.start", concept=concept_name, depth=state.get("depth", 0))

    # ---- Step 1: gather evidence -------------------------------------------
    pg = get_pg_client()
    velocity = await pg.mention_velocity(concept_name, window_days=90)

    # Mention count by source type (rough source-diversity signal)
    pool = await pg._ensure()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_type::text AS st, COUNT(*)::int AS n
            FROM mention_events
            WHERE concept_name = $1
            GROUP BY source_type
            """,
            concept_name,
        )
    mentions_by_source = {r["st"]: r["n"] for r in rows}
    total_mentions = sum(mentions_by_source.values())
    source_diversity = len(mentions_by_source)

    evidence = {
        "concept_name": concept_name,
        "total_mentions": total_mentions,
        "source_diversity": source_diversity,
        "mentions_by_source": mentions_by_source,
        "mention_velocity": velocity,
    }
    log.info("roma.execute.evidence_gathered", **evidence)

    # ---- Step 2: SLM call with structured output ---------------------------
    slm = get_slm_client()
    prompt_evidence = json.dumps(evidence, default=str, indent=2)

    system_prompt = (
        "You are a forecaster specialized in agentic-AI ecosystem dynamics. "
        "Given evidence about a tracked concept, produce a forward-looking "
        "prediction for its trajectory over 3, 6, and 12 month horizons. "
        "Be concrete: 'will X' or 'will not X' or 'partially Y.' Avoid hedging. "
        "Rate your confidence honestly — low confidence is fine when evidence "
        "is thin.\n\n"
        "Respond ONLY with valid JSON in this exact shape, no prose, no fences:\n"
        '{"prediction": "...", "confidence": 0.0-1.0, "horizon_months": 3|6|12, '
        '"reasoning": "...", "cited_concept_ids": [...]}'
    )

    user_prompt = (
        f"CONCEPT: {concept_name}\n\n"
        f"EVIDENCE:\n{prompt_evidence}\n\n"
        f"Forecast this concept's trajectory."
    )

    raw = await slm.generate(
        system=system_prompt, user=user_prompt,
        max_tokens=600, temperature=0.2,
    )

    # Defensive: strip markdown fences smaller models often emit
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()

    try:
        parsed = json.loads(raw)
        candidate = CandidateForecast(**parsed)
        log.info(
            "roma.execute.forecast_drafted",
            concept=concept_name,
            confidence=candidate.confidence,
        )
        return {
            "evidence": evidence,
            "candidate_forecast": candidate.model_dump(),
        }
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning(
            "roma.execute.bad_forecast_json",
            concept=concept_name, error=str(exc), raw=raw[:200],
        )
        # Return empty candidate; aggregator will produce a weak-band fallback
        return {"evidence": evidence, "candidate_forecast": {}}


# ---- Aggregator -----------------------------------------------------------


def aggregate(state: ForecastState) -> dict[str, Any]:
    """
    Finalize the forecast: assign confidence band, build the persisted
    forecast object.

    In the atomic case, there's only one path — we just bandify and persist.
    In the composite case (Session 2), this combines subtask_results into
    a multi-concept digest.
    """
    candidate = state.get("candidate_forecast", {})
    evidence = state.get("evidence", {})
    task = state["task"]

    if not candidate:
        # Fallback: SLM failed; produce a weak-band "insufficient signal" forecast
        log.info("roma.aggregate.no_candidate_using_weak_fallback")
        final_forecast = {
            "concept_name": task.get("concept_name"),
            "prediction": "Insufficient signal to forecast.",
            "confidence": 0.0,
            "reasoning": "Forecaster SLM did not produce a parseable response.",
            "cited_concept_ids": [],
            "evidence_snapshot": evidence,
        }
        return {
            "final_forecast": final_forecast,
            "confidence_band": "weak",
        }

    # Confidence band: 0.0-0.4 = weak, 0.4-0.7 = medium, 0.7-1.0 = high
    raw_confidence = float(candidate.get("confidence", 0.0))
    if raw_confidence < 0.4:
        band = "weak"
    elif raw_confidence < 0.7:
        band = "medium"
    else:
        band = "high"

    final_forecast = {
        "concept_name": task.get("concept_name"),
        "prediction": candidate.get("prediction", ""),
        "confidence": raw_confidence,
        "horizon_months": candidate.get("horizon_months", 6),    # NEW
        "reasoning": candidate.get("reasoning", ""),
        "cited_concept_ids": candidate.get("cited_concept_ids", []),
        "evidence_snapshot": evidence,
    }
    log.info(
        "roma.aggregate.done",
        concept=task.get("concept_name"),
        band=band, confidence=raw_confidence,
    )
    return {"final_forecast": final_forecast, "confidence_band": band}


# ---- Routing functions (pure conditional edges) -------------------------


def route_after_atomize(state: ForecastState) -> str:
    """Atomic → executor; composite → planner."""
    return "execute" if state.get("is_atomic", False) else "plan"


def route_after_plan(state: ForecastState) -> str:
    """After planning, the executor handles each subtask in turn.
    Session 1: planner is a no-op so we route directly to aggregator
    with empty subtask_results."""
    subtasks = state.get("subtasks", [])
    if not subtasks:
        return "aggregate"
    return "execute"  # Session 2 will actually recurse here