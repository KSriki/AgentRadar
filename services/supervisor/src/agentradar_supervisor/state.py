"""
ROMA orchestration state.

ForecastState flows through the LangGraph graph. Atomic tasks populate
final_forecast; composite tasks populate subtask_results during recursion
and final_digest/final_topn during aggregation.

The reducer annotation on subtask_results (Annotated[list, operator.add])
matters because composite execution appends results as it recurses. In
sequential execution today this is just additive; if we ever parallelize,
the framework's merge semantics keep it safe.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


# ---- Task descriptors ----------------------------------------------------


class ForecastTask(TypedDict, total=False):
    """A task ROMA orchestrates. The kind field discriminates."""

    kind: Literal["forecast.concept", "forecast.top_n", "forecast.digest"]

    # for forecast.concept
    concept_name: str

    # for forecast.top_n + forecast.digest
    top_n: int

    # for forecast.digest only — humans-readable label for this run
    digest_label: str


# ---- Orchestration state -------------------------------------------------


class ForecastState(TypedDict, total=False):
    # ---- Input ----
    task: ForecastTask
    depth: int
    trace_id: str
    parent_context: dict[str, Any]   # distilled context from parent invocation
    mcp: Any                          # fastmcp Client passed through state

    # ---- Atomizer ----
    is_atomic: bool

    # ---- Planner ----
    subtasks: list[ForecastTask]

    # Reducer-annotated: composite execution appends results as it recurses
    subtask_results: Annotated[list[dict[str, Any]], operator.add]

    # ---- Executor (atomic) ----
    evidence: dict[str, Any]
    candidate_forecast: dict[str, Any]

    # ---- Aggregator ----
    final_forecast: dict[str, Any]            # for forecast.concept
    final_topn: list[dict[str, Any]]          # for forecast.top_n
    final_digest: dict[str, Any]              # for forecast.digest
    confidence_band: Literal["weak", "medium", "high"]


# ---- Structured outputs --------------------------------------------------


class CandidateForecast(BaseModel):
    """Structured-output schema for the SLM's atomic forecast."""

    prediction: str = Field(description="Single trajectory prediction.")
    confidence: float = Field(ge=0.0, le=1.0)
    horizon_months: int = Field(ge=1, le=24, default=6)
    reasoning: str = Field(description="Why this prediction.")
    cited_concept_ids: list[str] = Field(default_factory=list)


class DigestSynthesis(BaseModel):
    """Structured output for the digest's narrative-summary SLM call."""

    themes: str = Field(
        description=(
            "2-4 sentence overview of patterns across the forecasts. "
            "Concrete observations, not generalities. Reference specific "
            "concepts by name."
        )
    )
    standout: str = Field(
        description="The single most notable forecast and why it matters."
    )