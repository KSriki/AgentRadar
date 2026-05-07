"""
Load Scout-related config from disk and graph state.

YAML files provide curated static queries. The query planner adds
graph-derived queries based on what the system has observed. The two
are concatenated, deduplicated, and presented as a single pool to
the supervisor's round-robin scheduler.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentradar_core import get_logger, settings
from agentradar_supervisor.query_planner import derive_tavily_queries

log = get_logger(__name__)


def _resolve_path(relative_or_absolute: str) -> Path:
    p = Path(relative_or_absolute)
    if p.is_absolute():
        return p
    return Path.cwd() / p


def _load_static_queries() -> list[str]:
    """Load the curated query list from the YAML config file."""
    path = _resolve_path(settings.scout.tavily_queries_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Tavily queries file not found: {path}. "
            f"Override SCOUT_TAVILY_QUERIES_PATH or create the file."
        )

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Tavily queries file is not valid YAML: {path}") from exc

    if not isinstance(data, dict) or "queries" not in data:
        raise ValueError(
            f"Tavily queries file must contain a top-level 'queries' list: {path}"
        )

    queries = data["queries"]
    if not isinstance(queries, list) or not queries:
        raise ValueError(f"Tavily queries file's 'queries' must be a non-empty list: {path}")

    cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
    if not cleaned:
        raise ValueError(f"Tavily queries file has no usable queries: {path}")

    log.info("scout.static_queries_loaded", path=str(path), count=len(cleaned))
    return cleaned


async def load_tavily_queries(include_derived: bool = True) -> list[str]:
    """
    Return the full pool of Tavily Scout queries: static (YAML-curated)
    plus optional graph-derived (computed from current store state).

    If include_derived=False, returns static only — useful for tests and
    for cold-start runs when the graph is empty.

    Failures in graph derivation are caught and logged: the static set
    is always returned even if Postgres or Neo4j are temporarily down.
    """
    static = _load_static_queries()

    if not include_derived:
        return static

    try:
        derived = await derive_tavily_queries()
    except Exception as exc:
        log.warning("scout.derived_queries_failed", error=str(exc))
        derived = []

    # Deduplicate; static queries take precedence (appear first)
    static_set = set(static)
    combined = static + [q for q in derived if q not in static_set]
    log.info(
        "scout.queries_combined",
        static=len(static), derived=len(derived), total=len(combined),
    )
    return combined