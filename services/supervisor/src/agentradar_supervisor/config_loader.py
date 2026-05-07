"""
Load Scout-related config files from disk.

These helpers are deliberately separate from `schedule.py`'s
pydantic-settings config — files are reloadable on supervisor restart
without rebuilding the container, while env-driven settings travel with
the container. Different lifecycles, different mechanisms.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentradar_core import get_logger, settings

log = get_logger(__name__)


def _resolve_path(relative_or_absolute: str) -> Path:
    """
    Resolve config paths. Absolute paths used as-is; relative paths
    resolved against the current working directory (which in the
    supervisor container is /app, the repo root mounted at WORKDIR).
    """
    p = Path(relative_or_absolute)
    if p.is_absolute():
        return p
    return Path.cwd() / p


def load_tavily_queries() -> list[str]:
    """
    Load the Tavily Scout's query list from YAML.

    Returns a non-empty list of queries. Raises if the file is missing,
    malformed, or empty — the Scout cannot run without queries, and
    failing fast surfaces the misconfiguration immediately.
    """
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

    log.info("scout.tavily_queries_loaded", path=str(path), count=len(cleaned))
    return cleaned