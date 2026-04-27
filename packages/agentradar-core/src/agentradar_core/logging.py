"""
Structured logging via structlog.

In an autonomous system, logs are forensic evidence. They need to be:
  - Structured (JSON in prod) so you can grep/aggregate by trace_id
  - Contextual (every log carries the trace_id of the supervisor invocation)
  - Cheap to write (no expensive formatting in hot paths)

Usage:
    from agentradar_core import get_logger, configure_logging
    configure_logging()                      # call once at process startup
    log = get_logger(__name__)
    log.info("scout.fetched", source="arxiv", count=12, trace_id=tid)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from agentradar_core.config import settings


def configure_logging(
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """
    Configure structlog + stdlib logging. Idempotent — safe to call multiple times.

    Args:
        level: log level name; defaults to settings.log_level
        json_output: if True, emit JSON; if False, pretty console output;
            if None, use JSON in non-local environments
    """
    effective_level = (level or settings.log_level).upper()
    if json_output is None:
        json_output = settings.environment != "local"

    # Stdlib logging — structlog wraps this for the actual emit.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=effective_level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,  # picks up trace_id from contextvars
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[effective_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to the given name (typically __name__)."""
    return structlog.get_logger(name)


def bind_trace_id(trace_id: str) -> None:
    """
    Bind a trace_id to the current async context. Every log emitted by any
    code in the same task/coroutine will automatically include it.

    Call this at the top of supervisor.invoke() with the run's trace_id.
    """
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def clear_trace_context() -> None:
    """Clear contextvars; call at the end of a supervisor run."""
    structlog.contextvars.clear_contextvars()