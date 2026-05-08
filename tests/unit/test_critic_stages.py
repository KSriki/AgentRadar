"""
Unit tests for Critic agent's three-stage validation pipeline.

Tests cover:
  - Stage 1 (structural): regex, empty fields, missing source_id
  - Stage 2 (ontology): predicate membership in known set
  - Stage 3 (faithfulness): SLM response parsing, dispatch by source-prefix
  - End-to-end review_one orchestration

External dependencies are mocked: SLM, S3, MCP. The Critic class itself
is exercised against real method calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentradar_supervisor.agents.critic import (
    Critic,
    KNOWN_PREDICATES,
    TripleToReview,
    _CYPHER_IDENT,
)


# ---- Helpers --------------------------------------------------------------


def make_triple(**overrides) -> TripleToReview:
    """Build a TripleToReview with sensible defaults; override per-test."""
    defaults = {
        "triple_id": "test-id-1",
        "subject": "MCP",
        "predicate": "MENTIONED_IN",
        "object": "arxiv:2401.12345",
        "source_id": "arxiv:2401.12345",
        "confidence": 0.6,
        "proposer_agent": "test-scout",
    }
    return TripleToReview(**{**defaults, **overrides})


# ---- Stage 1: structural --------------------------------------------------


class TestStructuralCheck:
    """Pure-Python validation of triple shape — no external deps."""

    def test_valid_triple_passes(self):
        critic = Critic()
        ok, reason = critic._structural_check(make_triple())
        assert ok is True
        assert reason is None

    def test_empty_subject_rejected(self):
        critic = Critic()
        ok, reason = critic._structural_check(make_triple(subject=""))
        assert ok is False
        assert "subject" in reason.lower()

    def test_whitespace_only_subject_rejected(self):
        critic = Critic()
        ok, reason = critic._structural_check(make_triple(subject="   "))
        assert ok is False

    def test_empty_object_rejected(self):
        critic = Critic()
        ok, reason = critic._structural_check(make_triple(object=""))
        assert ok is False
        assert "object" in reason.lower()

    def test_missing_source_id_rejected(self):
        critic = Critic()
        ok, reason = critic._structural_check(make_triple(source_id=""))
        assert ok is False
        assert "source_id" in reason.lower()

    @pytest.mark.parametrize("bad_predicate", [
        "lower_case",          # must start with uppercase
        "Has_Spaces in it",    # no spaces
        "WITH-DASH",           # no dashes
        "1STARTS_WITH_DIGIT",  # must start with letter
        "",                    # empty
        "A" * 65,              # too long (regex caps at 64)
    ])
    def test_invalid_predicate_rejected(self, bad_predicate):
        critic = Critic()
        ok, reason = critic._structural_check(make_triple(predicate=bad_predicate))
        assert ok is False
        assert "predicate" in reason.lower()

    @pytest.mark.parametrize("good_predicate", [
        "X",                                 # minimum: single uppercase letter
        "MENTIONED_IN",
        "INTRODUCED_BY",
        "A1234",
        "A" * 64,                            # exactly at the regex limit
    ])
    def test_valid_predicate_accepted(self, good_predicate):
        critic = Critic()
        # Use the regex directly to verify shape, since structural_check ALSO
        # requires the predicate to pass — but ontology might reject novel ones.
        # Here we're testing the regex layer, not ontology.
        assert _CYPHER_IDENT.match(good_predicate) is not None


# ---- Stage 2: ontology ----------------------------------------------------


class TestOntologyCheck:
    """Predicate must be in our known ontology."""

    @pytest.mark.parametrize("predicate", sorted(KNOWN_PREDICATES))
    def test_known_predicates_accepted(self, predicate):
        critic = Critic()
        triple = make_triple(predicate=predicate)
        ok, reason = critic._ontology_check(triple)
        assert ok is True
        assert reason is None

    def test_unknown_predicate_rejected(self):
        critic = Critic()
        triple = make_triple(predicate="HALLUCINATED_RELATIONSHIP")
        ok, reason = critic._ontology_check(triple)
        assert ok is False
        assert "ontology" in reason.lower()
        assert "HALLUCINATED_RELATIONSHIP" in reason


# ---- Stage 3: faithfulness ------------------------------------------------


class TestFaithfulnessCheck:
    """SLM-driven check — mocked SLM, mocked S3."""

    @pytest.mark.asyncio
    async def test_approved_response_parsed(self, mock_slm, monkeypatch):
        # Mock S3 to return a fake arxiv artifact
        fake_payload = json.dumps({
            "title": "MCP: Model Context Protocol",
            "summary": "Anthropic introduces MCP for agent tool integration."
        })

        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=fake_payload.encode())
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        # Queue the SLM response
        mock_slm.responses = [json.dumps({
            "verdict": "approved",
            "reasoning": "Source explicitly states MCP introduced by Anthropic",
            "confidence": 0.9,
        })]

        critic = Critic()
        triple = make_triple(
            subject="MCP", predicate="INTRODUCED_BY", object="Anthropic",
            source_id="arxiv:2401.12345",
        )
        approved, reasoning, confidence = await critic._faithfulness_check(triple)

        assert approved is True
        assert "MCP" in reasoning
        assert confidence == 0.9
        assert len(mock_slm.calls) == 1
        assert "TITLE" in mock_slm.calls[0]["user"]
        assert "MCP" in mock_slm.calls[0]["user"]

    @pytest.mark.asyncio
    async def test_rejected_response_parsed(self, mock_slm, monkeypatch):
        fake_payload = json.dumps({
            "title": "Some unrelated paper",
            "summary": "Discusses transformer architectures.",
        })
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=fake_payload.encode())
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        mock_slm.responses = [json.dumps({
            "verdict": "rejected",
            "reasoning": "Source does not mention MCP",
            "confidence": 0.85,
        })]

        critic = Critic()
        triple = make_triple(source_id="arxiv:9999.99999")
        approved, reasoning, _ = await critic._faithfulness_check(triple)

        assert approved is False
        assert "does not mention" in reasoning

    @pytest.mark.asyncio
    async def test_malformed_json_rejects_safely(self, mock_slm, monkeypatch):
        fake_payload = json.dumps({"title": "X", "summary": "Y"})
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=fake_payload.encode())
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        # SLM returns garbage
        mock_slm.responses = ["this is not json at all { broken"]

        critic = Critic()
        approved, reasoning, _ = await critic._faithfulness_check(make_triple())

        assert approved is False
        assert "unparseable" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_handled(self, mock_slm, monkeypatch):
        """Smaller models sometimes wrap JSON in ```json ... ``` despite instructions."""
        fake_payload = json.dumps({"title": "X", "summary": "Y"})
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=fake_payload.encode())
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        mock_slm.responses = [
            '```json\n{"verdict":"approved","reasoning":"ok","confidence":0.8}\n```'
        ]

        critic = Critic()
        approved, _, _ = await critic._faithfulness_check(make_triple())
        assert approved is True

    @pytest.mark.asyncio
    async def test_invalid_verdict_rejects(self, mock_slm, monkeypatch):
        fake_payload = json.dumps({"title": "X", "summary": "Y"})
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=fake_payload.encode())
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        mock_slm.responses = [json.dumps({
            "verdict": "maybe", "reasoning": "?", "confidence": 0.5,
        })]

        critic = Critic()
        approved, reasoning, _ = await critic._faithfulness_check(make_triple())
        assert approved is False
        assert "invalid verdict" in reasoning.lower()

    @pytest.mark.asyncio
    async def test_unfetchable_source_rejects(self, monkeypatch):
        """If S3 raises, faithfulness check rejects without calling SLM."""
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(side_effect=Exception("bucket down"))
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        critic = Critic()
        approved, reasoning, _ = await critic._faithfulness_check(make_triple())
        assert approved is False
        assert "could not fetch" in reasoning.lower()


# ---- Source-prefix dispatch ----------------------------------------------


class TestSourcePrefixDispatch:
    """The Critic dispatches to different artifact shapes by source-id prefix."""

    @pytest.mark.asyncio
    async def test_arxiv_prefix_renders_title_and_abstract(self, monkeypatch):
        payload = json.dumps({"title": "T", "summary": "A"}).encode()
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=payload)
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        critic = Critic()
        text = await critic._fetch_source_text("arxiv:2401.12345")
        assert text is not None
        assert "TITLE: T" in text
        assert "ABSTRACT: A" in text

    @pytest.mark.asyncio
    async def test_tavily_prefix_renders_title_url_content(self, monkeypatch):
        payload = json.dumps({
            "title": "T", "url": "https://x.com", "content": "C",
        }).encode()
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=payload)
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )

        critic = Critic()
        text = await critic._fetch_source_text("tavily:abc123")
        assert text is not None
        assert "TITLE: T" in text
        assert "URL: https://x.com" in text
        assert "CONTENT: C" in text

    @pytest.mark.asyncio
    async def test_unknown_prefix_returns_none(self, monkeypatch):
        critic = Critic()
        text = await critic._fetch_source_text("unknown-source:xyz")
        assert text is None

    @pytest.mark.asyncio
    async def test_malformed_source_id_returns_none(self, monkeypatch):
        critic = Critic()
        text = await critic._fetch_source_text("no-colon-here")
        assert text is None


# ---- End-to-end review_one ------------------------------------------------


class TestReviewOne:
    """Test the full three-stage orchestration with all stages mocked."""

    @pytest.mark.asyncio
    async def test_structural_failure_short_circuits(self, mock_mcp):
        """Bad structural shape should reject without hitting ontology or SLM."""
        critic = Critic(dry_run=True)
        triple = make_triple(subject="")  # structural will fail
        result = await critic._review_one(mock_mcp, triple)
        assert result["decision"] == "rejected"
        assert result["stage"] == "structural"

    @pytest.mark.asyncio
    async def test_ontology_failure_short_circuits(self, mock_mcp):
        critic = Critic(dry_run=True)
        triple = make_triple(predicate="UNKNOWN_PREDICATE")
        result = await critic._review_one(mock_mcp, triple)
        assert result["decision"] == "rejected"
        assert result["stage"] == "ontology"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_mcp(self, mock_mcp, mock_slm, monkeypatch):
        """In dry-run mode, no approve_triple/reject_triple calls are made."""
        payload = json.dumps({"title": "T", "summary": "MCP from Anthropic"}).encode()
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=payload)
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )
        mock_slm.responses = [json.dumps({
            "verdict": "approved", "reasoning": "yes", "confidence": 0.9,
        })]

        critic = Critic(dry_run=True)
        await critic._review_one(mock_mcp, make_triple())

        # In dry run, NO MCP calls should have been made
        approve_or_reject = [
            c for c in mock_mcp.calls
            if c["tool"] in ("approve_triple", "reject_triple")
        ]
        assert len(approve_or_reject) == 0

    @pytest.mark.asyncio
    async def test_approval_calls_approve_triple(self, mock_mcp, mock_slm, monkeypatch):
        payload = json.dumps({"title": "T", "summary": "S"}).encode()
        mock_s3 = MagicMock()
        mock_s3.get_artifact = AsyncMock(return_value=payload)
        monkeypatch.setattr(
            "agentradar_supervisor.agents.critic.get_s3_client",
            lambda: mock_s3,
        )
        mock_slm.responses = [json.dumps({
            "verdict": "approved", "reasoning": "yes", "confidence": 0.9,
        })]

        critic = Critic(dry_run=False)
        await critic._review_one(mock_mcp, make_triple())

        approve_calls = [c for c in mock_mcp.calls if c["tool"] == "approve_triple"]
        assert len(approve_calls) == 1

    @pytest.mark.asyncio
    async def test_rejection_calls_reject_triple_with_stage(
        self, mock_mcp, mock_slm, monkeypatch,
    ):
        critic = Critic(dry_run=False)
        triple = make_triple(predicate="HALLUCINATED")  # ontology rejection
        await critic._review_one(mock_mcp, triple)

        reject_calls = [c for c in mock_mcp.calls if c["tool"] == "reject_triple"]
        assert len(reject_calls) == 1
        # Stage should appear in the reason for diagnostics
        assert "[ontology]" in reject_calls[0]["args"]["reason"]