"""Unit tests for agentradar_core.logging."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from agentradar_core import bind_trace_id, clear_trace_context, configure_logging, get_logger


class TestLoggingConfiguration:
    def test_configure_idempotent(self) -> None:
        # Call twice — should not raise or double-register processors.
        configure_logging(level="DEBUG", json_output=True)
        configure_logging(level="DEBUG", json_output=True)
        log = get_logger("test")
        assert log is not None

    def test_trace_id_propagates(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Bound trace_id should appear in subsequent log records."""
        configure_logging(level="INFO", json_output=True)
        log = get_logger("test_trace")

        bind_trace_id("trace-12345")
        try:
            log.info("did_a_thing", agent="scout")
        finally:
            clear_trace_context()

        captured = capsys.readouterr().out.strip().splitlines()
        # The last line is our log; parse it as JSON.
        record = json.loads(captured[-1])
        assert record["trace_id"] == "trace-12345"
        assert record["agent"] == "scout"
        assert record["event"] == "did_a_thing"

    def test_clear_trace_context_removes_id(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level="INFO", json_output=True)
        log = get_logger("test_clear")

        bind_trace_id("should-not-appear")
        clear_trace_context()
        log.info("after_clear")

        captured = capsys.readouterr().out.strip().splitlines()
        record = json.loads(captured[-1])
        assert "trace_id" not in record