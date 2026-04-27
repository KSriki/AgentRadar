"""
Shared domain types. Pydantic models for things that cross process boundaries
(MCP tool args/returns, agent messages); plain TypedDicts for things that stay
inside one process (LangGraph state, internal handoffs).

Rule of thumb: if it's serialized to JSON or stored in a DB, make it a
pydantic BaseModel. If it only exists inside one async function or graph
invocation, a TypedDict is enough.
"""

from __future__ import annotations

import operator
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums — string-valued so they round-trip through JSON cleanly
# ---------------------------------------------------------------------------


class ConceptType(StrEnum):
    PROTOCOL = "Protocol"
    FRAMEWORK = "Framework"
    PATTERN = "Pattern"
    MODEL = "Model"
    TOOL = "Tool"
    UNKNOWN = "Unknown"


class SourceType(StrEnum):
    ARXIV = "arxiv"
    GITHUB = "github"
    BLOG = "blog"
    SPEC = "spec"
    CONFERENCE = "conference"
    RFC = "rfc"
    OTHER = "other"


class TripleStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ForecastConfidence(StrEnum):
    HIGH = "high"      # >= 0.70
    MEDIUM = "medium"  # 0.40 - 0.69
    WEAK = "weak"      # < 0.40

    @classmethod
    def from_score(cls, score: float) -> ForecastConfidence:
        if score >= 0.70:
            return cls.HIGH
        if score >= 0.40:
            return cls.MEDIUM
        return cls.WEAK


# ---------------------------------------------------------------------------
# Domain models — pydantic for serialization safety
# ---------------------------------------------------------------------------


class Source(BaseModel):
    """Raw artifact provenance: where a claim came from."""

    model_config = ConfigDict(frozen=True)  # immutable once created

    id: str  # stable hash or URL
    type: SourceType
    url: str | None = None
    title: str | None = None
    observed_at: datetime
    raw_artifact_uri: str | None = None  # e.g., s3://agentradar-artifacts/...


class Triple(BaseModel):
    """
    A (subject, predicate, object) claim with provenance and confidence.
    The Critic gates these before they become Neo4j relationships.
    """

    subject: str
    predicate: str
    object: str
    source_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    proposer_agent: str


class PendingTriple(Triple):
    """A Triple as it sits in the pending_triples table awaiting Critic decision."""

    id: UUID
    proposal_hash: str
    status: TripleStatus = TripleStatus.PENDING
    rejection_reason: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class Forecast(BaseModel):
    """
    A prediction the Forecaster makes about a concept's future significance.
    Graded later by the Calibrator for self-calibration.
    """

    id: UUID
    concept_name: str
    claim: str  # human-readable forecast text
    confidence: float = Field(ge=0.0, le=1.0)
    horizon_months: int = Field(ge=1, le=24)
    cited_source_ids: list[str]
    predicted_at: datetime
    # Filled in by the Calibrator after horizon elapses
    outcome: Literal["hit", "miss", "partial"] | None = None
    graded_at: datetime | None = None
    graded_notes: str | None = None

    @property
    def confidence_band(self) -> ForecastConfidence:
        return ForecastConfidence.from_score(self.confidence)


# ---------------------------------------------------------------------------
# Agent / supervisor state types — TypedDicts for LangGraph
# ---------------------------------------------------------------------------


class TaskSpec(TypedDict):
    """A unit of work flowing through the ROMA supervisor."""

    id: str
    goal: str
    constraints: dict[str, Any]
    depth: int  # current recursion depth; capped in the supervisor


class ROMAState(TypedDict, total=False):
    """
    Working state for one supervisor invocation.

    `total=False` means all keys are optional, which matches LangGraph's
    pattern of building state up across nodes. The reducer annotation on
    `subtask_results` makes appends merge instead of overwrite.
    """

    task: TaskSpec
    is_atomic: bool
    plan: list[TaskSpec]
    subtask_results: Annotated[list[dict[str, Any]], operator.add]
    final_result: dict[str, Any]
    parent_context: dict[str, Any]  # DISTILLED summary, not full parent state
    trace_id: str