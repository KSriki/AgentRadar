"""Unit tests for agentradar_core.types — pure pydantic + enums."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentradar_core import (
    ConceptType,
    Forecast,
    ForecastConfidence,
    PendingTriple,
    Source,
    SourceType,
    Triple,
    TripleStatus,
)


class TestEnums:
    def test_concept_type_str_value(self) -> None:
        # StrEnum: the value IS a string for serialization purposes.
        assert ConceptType.PROTOCOL == "Protocol"
        assert json.dumps({"t": ConceptType.PROTOCOL.value}) == '{"t": "Protocol"}'

    def test_source_type_lowercase(self) -> None:
        assert SourceType.ARXIV == "arxiv"
        assert SourceType.GITHUB == "github"

    def test_triple_status_values(self) -> None:
        assert TripleStatus.PENDING == "pending"
        assert TripleStatus.APPROVED == "approved"
        assert TripleStatus.REJECTED == "rejected"


class TestForecastConfidence:
    @pytest.mark.parametrize(
        "score, expected",
        [
            (0.0, ForecastConfidence.WEAK),
            (0.39, ForecastConfidence.WEAK),
            (0.40, ForecastConfidence.MEDIUM),
            (0.69, ForecastConfidence.MEDIUM),
            (0.70, ForecastConfidence.HIGH),
            (1.0, ForecastConfidence.HIGH),
        ],
    )
    def test_band_thresholds(
        self, score: float, expected: ForecastConfidence
    ) -> None:
        assert ForecastConfidence.from_score(score) == expected


class TestSource:
    def test_source_is_immutable(self) -> None:
        src = Source(
            id="abc", type=SourceType.ARXIV, observed_at=datetime.now(UTC)
        )
        with pytest.raises(ValidationError):
            src.id = "different"  # type: ignore[misc]

    def test_source_round_trip_json(self) -> None:
        src = Source(
            id="arxiv:2401.12345",
            type=SourceType.ARXIV,
            url="https://arxiv.org/abs/2401.12345",
            title="Some Paper",
            observed_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        # Serialize and re-parse — should be byte-identical semantics.
        payload = src.model_dump_json()
        reparsed = Source.model_validate_json(payload)
        assert reparsed == src


class TestTriple:
    def test_confidence_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Triple(
                subject="A", predicate="X", object="B",
                source_id="s1", confidence=1.5, proposer_agent="scout",
            )
        with pytest.raises(ValidationError):
            Triple(
                subject="A", predicate="X", object="B",
                source_id="s1", confidence=-0.1, proposer_agent="scout",
            )

    def test_confidence_endpoints_allowed(self) -> None:
        for c in (0.0, 1.0):
            t = Triple(
                subject="A", predicate="X", object="B",
                source_id="s1", confidence=c, proposer_agent="scout",
            )
            assert t.confidence == c


class TestPendingTriple:
    def test_inherits_triple_validation(self) -> None:
        with pytest.raises(ValidationError):
            PendingTriple(
                id=uuid4(), proposal_hash="h",
                subject="A", predicate="X", object="B",
                source_id="s1", confidence=2.0, proposer_agent="scout",
                created_at=datetime.now(UTC),
            )


class TestForecast:
    def test_confidence_band_property(self) -> None:
        f = Forecast(
            id=uuid4(), concept_name="MCP",
            claim="MCP will see major-cloud reference impls",
            confidence=0.85, horizon_months=3,
            cited_source_ids=["s1", "s2"],
            predicted_at=datetime.now(UTC),
        )
        assert f.confidence_band == ForecastConfidence.HIGH

    def test_horizon_months_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Forecast(
                id=uuid4(), concept_name="X", claim="...", confidence=0.5,
                horizon_months=0,  # below min
                cited_source_ids=[], predicted_at=datetime.now(UTC),
            )
        with pytest.raises(ValidationError):
            Forecast(
                id=uuid4(), concept_name="X", claim="...", confidence=0.5,
                horizon_months=25,  # above max
                cited_source_ids=[], predicted_at=datetime.now(UTC),
            )