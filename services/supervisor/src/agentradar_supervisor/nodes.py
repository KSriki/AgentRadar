"""
ROMA node implementations.

Atomizer / Planner / Executor / Aggregator over ForecastState. The
shape from Session 1 is preserved; Session 2 adds composite-task
handling without changing the graph topology.

Per the SLM-where-needed principle:
- Atomizer / Planner / routing: deterministic Python (no LLM)
- Executor: SLM-bound for atomic; recurses for composite
- Aggregator: deterministic for top_n; SLM for digest narrative
"""

from __future__ import annotations

import json
from typing import Any

from agentradar_core import get_logger
from agentradar_store import get_slm_client

from agentradar_supervisor.state import (
    CandidateForecast,
    DigestSynthesis,
    ForecastState,
    ForecastTask,
)

log = get_logger(__name__)


MAX_DEPTH = 3


# ---- Atomizer ------------------------------------------------------------


def atomize(state: ForecastState) -> dict[str, Any]:
    """
    Decide atomic vs composite. forecast.concept is the only atomic kind;
    everything else decomposes. Depth cap forces atomicity to prevent
    runaway recursion.
    """
    task = state["task"]
    kind = task.get("kind", "forecast.concept")
    depth = state.get("depth", 0)

    if depth >= MAX_DEPTH:
        log.warning("roma.atomize.depth_cap_reached", depth=depth, kind=kind)
        return {"is_atomic": True}

    if kind == "forecast.concept":
        return {"is_atomic": True}
    return {"is_atomic": False}


# ---- Planner -------------------------------------------------------------


async def plan(state: ForecastState) -> dict[str, Any]:
    """
    Decompose composite tasks into subtasks.

    forecast.top_n: query the graph for N best candidates; emit N
                    forecast.concept subtasks.
    forecast.digest: emit one forecast.top_n subtask (which itself will
                     recurse) plus internal state for the eventual
                     narrative synthesis step.
    """
    task = state["task"]
    kind = task.get("kind")
    mcp = state.get("mcp")

    if mcp is None:
        log.error("roma.plan.no_mcp_in_state")
        return {"subtasks": []}

    if kind == "forecast.top_n":
        n = task.get("top_n", 5)
        try:
            result = await mcp.call_tool(
                "select_top_n_concepts",
                {
                    "top_n": n,
                    "velocity_window_days": 90,
                    "cooldown_days": 14,
                },
            )
            concept_names = result.data.get("concept_names", [])
        except Exception as exc:
            log.exception("roma.plan.topn_select_failed", error=str(exc))
            return {"subtasks": []}

        subtasks: list[ForecastTask] = [
            {"kind": "forecast.concept", "concept_name": c} for c in concept_names
        ]
        log.info("roma.plan.topn_planned", count=len(subtasks), concepts=concept_names)
        return {"subtasks": subtasks}

    if kind == "forecast.digest":
        # Digest decomposes into ONE forecast.top_n subtask. After that
        # subtask resolves, the Aggregator picks up the list of forecasts
        # and runs the narrative-synthesis SLM call.
        n = task.get("top_n", 5)
        subtasks = [
            {"kind": "forecast.top_n", "top_n": n},
        ]
        log.info("roma.plan.digest_planned", inner_top_n=n)
        return {"subtasks": subtasks}

    log.warning("roma.plan.unknown_composite_kind", kind=kind)
    return {"subtasks": []}


# ---- Executor ------------------------------------------------------------


async def execute(state: ForecastState) -> dict[str, Any]:
    """
    Run atomic execution OR recurse on subtasks.

    Atomic path (forecast.concept): pull evidence via MCP, call SLM with
    structured-output schema, return candidate forecast.

    Composite path (subtasks present): recursively invoke ROMA graph for
    each subtask. Sequential execution — small models serialize anyway,
    and sequential traces are debuggable.
    """
    # If subtasks are present, this is the composite-recursion case
    subtasks = state.get("subtasks", [])
    if subtasks:
        return await _execute_subtasks(state, subtasks)

    # Otherwise atomic
    return await _execute_atomic(state)


async def _execute_subtasks(
    state: ForecastState,
    subtasks: list[ForecastTask],
) -> dict[str, Any]:
    """Recursively invoke ROMA for each subtask. Sequential."""
    # Lazy import to dodge a circular reference at module load
    from agentradar_supervisor.graph import get_roma_graph

    parent_kind = state["task"].get("kind")
    depth = state.get("depth", 0)
    mcp = state.get("mcp")
    parent_context = _distill_parent_context(state)

    log.info(
        "roma.execute.recurse_start",
        parent_kind=parent_kind,
        depth=depth,
        subtask_count=len(subtasks),
    )

    graph = get_roma_graph()
    results: list[dict[str, Any]] = []
    for i, sub in enumerate(subtasks):
        log.info(
            "roma.execute.recurse_subtask",
            parent_kind=parent_kind,
            subtask_index=i,
            subtask_kind=sub.get("kind"),
        )
        sub_state: ForecastState = {
            "task": sub,
            "depth": depth + 1,
            "trace_id": state.get("trace_id", "") + f".{i}",
            "parent_context": {**parent_context, "subtask_index": i},
            "mcp": mcp,
            "subtask_results": [],
        }
        try:
            sub_final = await graph.ainvoke(sub_state)
        except Exception as exc:
            log.warning(
                "roma.execute.subtask_failed",
                parent_kind=parent_kind,
                subtask_index=i,
                error=str(exc),
            )
            results.append({"error": str(exc), "subtask": sub})
            continue

        # Capture whichever final-field the child produced
        if sub_final.get("final_forecast"):
            results.append(
                {"forecast": sub_final["final_forecast"], "band": sub_final.get("confidence_band")}
            )
        elif sub_final.get("final_topn"):
            results.append({"topn": sub_final["final_topn"]})
        else:
            log.warning("roma.execute.subtask_no_final", subtask_index=i)
            results.append({"error": "no final state", "subtask": sub})

    log.info(
        "roma.execute.recurse_done",
        parent_kind=parent_kind,
        results_count=len(results),
    )
    return {"subtask_results": results}


async def _execute_atomic(state: ForecastState) -> dict[str, Any]:
    """The Session 1 atomic path: forecast one concept."""
    task = state["task"]
    if task.get("kind") != "forecast.concept":
        log.warning("roma.execute.unsupported_atomic_kind", kind=task.get("kind"))
        return {"evidence": {}, "candidate_forecast": {}}

    concept_name = task.get("concept_name", "")
    if not concept_name:
        log.warning("roma.execute.no_concept_name")
        return {"evidence": {}, "candidate_forecast": {}}

    mcp = state.get("mcp")
    if mcp is None:
        log.error("roma.execute.no_mcp_in_state")
        return {"evidence": {}, "candidate_forecast": {}}

    log.info("roma.execute.start", concept=concept_name, depth=state.get("depth", 0))

    # Gather evidence via MCP
    try:
        result = await mcp.call_tool(
            "get_forecast_evidence",
            {
                "concept_name": concept_name,
                "velocity_window_days": 90,
            },
        )
        evidence = result.data
    except Exception as exc:
        log.exception("roma.execute.evidence_failed", concept=concept_name, error=str(exc))
        return {"evidence": {}, "candidate_forecast": {}}

    log.info(
        "roma.execute.evidence_gathered",
        **{k: v for k, v in evidence.items() if k != "mention_velocity"},
    )

    # SLM call with structured output
    slm = get_slm_client()
    parent_ctx = state.get("parent_context", {})
    context_hint = (
        f"\n\nThis forecast is part of a top-{state.get('parent_context', {}).get('subtask_index', '')+1 if 'subtask_index' in parent_ctx else 'N'} "
        f"series within a digest."
        if "subtask_index" in parent_ctx
        else ""
    )
    system_prompt = (
        "You are a forecaster specialized in agentic-AI ecosystem dynamics. "
        "Given evidence about a tracked concept, produce a SINGLE forward-looking "
        "prediction. Be concrete: 'will X' or 'will not X' or 'partially Y'. "
        "Avoid hedging.\n\n"
        "The confidence field is a DECIMAL between 0.0 and 1.0 (not a percentage). "  # <-- explicit
        "Examples: 0.3 = low confidence, 0.6 = moderate, 0.85 = strong.\n\n"  # <-- examples help
        "The prediction field is ONE STRING (one prediction). "
        "The horizon_months field is ONE INTEGER from 1 to 24 — choose the "
        "horizon (3, 6, or 12 are common) that best fits available signal. "
        "Sparse evidence warrants longer horizons." + context_hint
    )
    user_prompt = (
        f"CONCEPT: {concept_name}\n\n"
        f"EVIDENCE:\n{json.dumps(evidence, default=str, indent=2)}\n\n"
        f"Forecast this concept's trajectory."
    )

    forecast_schema = {
        "type": "object",
        "properties": {
            "prediction": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "horizon_months": {"type": "integer", "minimum": 1, "maximum": 24},
            "reasoning": {"type": "string"},
            "cited_concept_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["prediction", "confidence", "horizon_months", "reasoning"],
    }

    raw = await slm.generate(
        system=system_prompt,
        user=user_prompt,
        max_tokens=600,
        temperature=0.2,
        response_format=forecast_schema,
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()

    try:
        parsed = json.loads(raw)

        # Normalize common SLM mistakes: confidence as percentage (0-100)
        # or unbounded integer instead of decimal in [0, 1]. The model's
        # intent is usually recoverable; weak-fallback is a stronger
        # response than this deserves.
        if "confidence" in parsed and isinstance(parsed["confidence"], int | float):
            c = float(parsed["confidence"])
            if c > 1.0:
                # 70 → 0.70, 7 → 0.7, 0.85 stays 0.85
                if c <= 10:
                    c = c / 10.0
                elif c <= 100:
                    c = c / 100.0
                else:
                    c = 1.0  # garbage; cap rather than fail
                log.info(
                    "roma.execute.confidence_normalized",
                    original=parsed["confidence"],
                    normalized=c,
                )
                parsed["confidence"] = c

        candidate = CandidateForecast(**parsed)
        log.info(
            "roma.execute.forecast_drafted", concept=concept_name, confidence=candidate.confidence
        )
        return {
            "evidence": evidence,
            "candidate_forecast": candidate.model_dump(),
        }
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning(
            "roma.execute.bad_forecast_json", concept=concept_name, error=str(exc), raw=raw[:200]
        )
        return {"evidence": evidence, "candidate_forecast": {}}


def _distill_parent_context(state: ForecastState) -> dict[str, Any]:
    """
    Pass DISTILLED parent context to recursive children — not the full
    parent state. This is the ROMA anti-context-bloat pattern: each
    child knows just enough about its context, not everything.
    """
    parent_kind = state["task"].get("kind")
    if parent_kind == "forecast.digest":
        return {
            "ancestor_kind": "digest",
            "audience": "weekly digest reader",
        }
    if parent_kind == "forecast.top_n":
        return {
            "ancestor_kind": "top_n",
            "total_in_series": state["task"].get("top_n", 5),
        }
    return {}


# ---- Aggregator ----------------------------------------------------------


async def aggregate(state: ForecastState) -> dict[str, Any]:
    """
    Finalize results based on task kind. Three paths:
    - forecast.concept (atomic): bandify confidence, build final_forecast
    - forecast.top_n: collect subtask_results into final_topn list
    - forecast.digest: collect inner top_n + SLM-synthesize narrative
    """
    task = state["task"]
    kind = task.get("kind", "forecast.concept")

    if kind == "forecast.concept":
        return _aggregate_concept(state)
    if kind == "forecast.top_n":
        return _aggregate_topn(state)
    if kind == "forecast.digest":
        return await _aggregate_digest(state)

    log.warning("roma.aggregate.unknown_kind", kind=kind)
    return {}


def _aggregate_concept(state: ForecastState) -> dict[str, Any]:
    """Atomic concept forecast (Session 1 path, unchanged)."""
    candidate = state.get("candidate_forecast", {})
    evidence = state.get("evidence", {})
    task = state["task"]

    if not candidate:
        log.info("roma.aggregate.no_candidate_using_weak_fallback")
        return {
            "final_forecast": {
                "concept_name": task.get("concept_name"),
                "prediction": "Insufficient signal to forecast.",
                "confidence": 0.0,
                "horizon_months": 6,
                "reasoning": "Forecaster SLM did not produce parseable response.",
                "cited_concept_ids": [],
                "evidence_snapshot": evidence,
            },
            "confidence_band": "weak",
        }

    raw_confidence = float(candidate.get("confidence", 0.0))
    band = "weak" if raw_confidence < 0.4 else "medium" if raw_confidence < 0.7 else "high"

    final_forecast = {
        "concept_name": task.get("concept_name"),
        "prediction": candidate.get("prediction", ""),
        "confidence": raw_confidence,
        "horizon_months": candidate.get("horizon_months", 6),
        "reasoning": candidate.get("reasoning", ""),
        "cited_concept_ids": candidate.get("cited_concept_ids", []),
        "evidence_snapshot": evidence,
    }
    log.info(
        "roma.aggregate.concept_done",
        concept=task.get("concept_name"),
        band=band,
        confidence=raw_confidence,
    )
    return {"final_forecast": final_forecast, "confidence_band": band}


def _aggregate_topn(state: ForecastState) -> dict[str, Any]:
    """Collect subtask results from N forecast.concept invocations."""
    results = state.get("subtask_results", [])
    final_topn = [r["forecast"] for r in results if "forecast" in r]
    log.info("roma.aggregate.topn_done", count=len(final_topn))
    return {"final_topn": final_topn}


async def _aggregate_digest(state: ForecastState) -> dict[str, Any]:
    """
    Combine the inner top_n result with a SLM-generated narrative.

    Hybrid approach: deterministic structure (rank, claims, confidences)
    plus a small focused SLM call for theme detection. The SLM has one
    bounded job — write a themes paragraph — rather than the whole digest.
    """
    results = state.get("subtask_results", [])
    inner = next((r for r in results if "topn" in r), None)
    if not inner:
        log.warning("roma.aggregate.digest_no_inner_topn")
        return {
            "final_digest": {
                "label": state["task"].get("digest_label", ""),
                "themes": "No forecasts available.",
                "standout": "",
                "forecasts": [],
            },
            "confidence_band": "weak",
        }

    forecasts = inner["topn"]
    if not forecasts:
        return {
            "final_digest": {
                "label": state["task"].get("digest_label", ""),
                "themes": "No qualifying concepts this week.",
                "standout": "",
                "forecasts": [],
            },
            "confidence_band": "weak",
        }

    # SLM synthesis — bounded job: themes paragraph + standout pick
    slm = get_slm_client()
    digest_input = json.dumps(
        [
            {
                "concept": f["concept_name"],
                "claim": f["prediction"],
                "confidence": f["confidence"],
                "horizon_months": f.get("horizon_months", 6),
            }
            for f in forecasts
        ],
        indent=2,
    )

    synthesis_schema = {
        "type": "object",
        "properties": {
            "themes": {"type": "string"},
            "standout": {"type": "string"},
        },
        "required": ["themes", "standout"],
    }
    system = (
        "You are an agentic-AI editor synthesizing a weekly digest. Given "
        "a list of forecasts, write a SINGLE PARAGRAPH (2-4 sentences) "
        "identifying cross-cutting themes — patterns you see across multiple "
        "forecasts, areas of convergence or divergence, what stands out as "
        "notable. Reference specific concept names. Avoid hedging.\n\n"
        "Then pick the SINGLE most notable forecast and explain in ONE "
        "SENTENCE why it matters most.\n\n"
        "IMPORTANT format constraints:\n"
        "- 'themes' is ONE STRING containing prose, NOT a list/array. "
        "Write complete sentences separated by periods, not bullet points.\n"
        "- 'standout' is ONE STRING containing one sentence about the most "
        "notable forecast.\n"
        "- Do NOT use brackets, quotes-around-items, or list syntax."
    )
    user = f"FORECASTS:\n{digest_input}\n\nSynthesize."

    try:
        raw = await slm.generate(
            system=system,
            user=user,
            max_tokens=400,
            temperature=0.3,
            response_format=synthesis_schema,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1].lstrip("json\n").rstrip("`").strip()
        synth = DigestSynthesis(**json.loads(raw))
        themes, standout = synth.themes, synth.standout
    except Exception as exc:
        log.warning("roma.aggregate.digest_synthesis_failed", error=str(exc))
        themes = f"This week's digest covers {len(forecasts)} concepts."
        standout = forecasts[0]["concept_name"] if forecasts else ""

    avg_confidence = sum(f["confidence"] for f in forecasts) / len(forecasts)
    band = "weak" if avg_confidence < 0.4 else "medium" if avg_confidence < 0.7 else "high"

    final_digest = {
        "label": state["task"].get("digest_label", ""),
        "themes": themes,
        "standout": standout,
        "forecasts": forecasts,
        "average_confidence": avg_confidence,
    }
    log.info(
        "roma.aggregate.digest_done",
        count=len(forecasts),
        band=band,
        average_confidence=avg_confidence,
    )
    return {"final_digest": final_digest, "confidence_band": band}


# ---- Routing functions ---------------------------------------------------


def route_after_atomize(state: ForecastState) -> str:
    return "execute" if state.get("is_atomic", False) else "plan"


def route_after_plan(state: ForecastState) -> str:
    return "execute" if state.get("subtasks") else "aggregate"
