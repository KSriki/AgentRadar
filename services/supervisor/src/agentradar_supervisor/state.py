"""
ROMA orchestration state.

ForecastState (and any future *State types for other workflows) is the
typed dict that flows through the LangGraph graph. Nodes read fields,
write fields, and the framework merges across parallel paths using the
reducer annotations (Annotated[..., operator.add]).

The schema is deliberately union-typed: a single graph supports multiple
workflows (forecast, future: investigate, etc.) by including their input
in this state but only the relevant node populating their output.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


# ---- Task descriptors -----------------------------------------------------


class ForecastTask(TypedDict, total=False):
    """A task ROMA needs to orchestrate."""

    kind: Literal["forecast.concept", "forecast.top_n", "forecast.digest"]
    # ^ which workflow runs. Session 1 supports forecast.concept only.
    concept_name: str  # for forecast.concept
    top_n: int         # for forecast.top_n (Session 2)


# ---- Orchestration state --------------------------------------------------


class ForecastState(TypedDict, total=False):
    """
    State flowing through ROMA for a Forecaster invocation.

    Fields are total=False because they're populated incrementally as the
    graph walks: input fields exist from the start, atomizer/planner add
    their outputs, executor adds evidence + draft, aggregator finalizes.
    """

    # ---- Input ----
    task: ForecastTask
    depth: int                       # recursion depth, 0 at top level
    trace_id: str
    parent_context: dict[str, Any]   # distilled parent context for recursion

    # ---- Atomizer's decision ----
    is_atomic: bool

    # ---- Planner's output (Session 2; unused in Session 1) ----
    subtasks: list[ForecastTask]
    subtask_results: Annotated[list[dict[str, Any]], operator.add]

    # ---- Executor's output (atomic case) ----
    evidence: dict[str, Any]              # facts gathered from the graph
    candidate_forecast: dict[str, Any]    # LLM's first draft

    # ---- Aggregator's output ----
    final_forecast: dict[str, Any]        # the persisted forecast object
    confidence_band: Literal["weak", "medium", "high"]

    mcp: Any


# ---- Output Pydantic models for structured SLM outputs --------------------


from pydantic import BaseModel, Field


class CandidateForecast(BaseModel):
    """Structured-output schema for the LLM's first-draft forecast."""

    prediction: str = Field(
        description="One-paragraph trajectory prediction for the concept "
                    "across 3, 6, and 12 month horizons."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Self-rated confidence in this prediction, 0.0 to 1.0."
    )
    horizon_months: int = Field(
        ge=1, le=24, default=6,
        description="Forecast horizon in months (1-24). Default 6.",
    )
    reasoning: str = Field(
        description="Brief explanation of which evidence most supports "
                    "or undermines the prediction."
    )
    cited_concept_ids: list[str] = Field(
        default_factory=list,
        description="Concept names or source IDs the prediction cites."
    )
    