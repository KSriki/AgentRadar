"""
One-off Forecaster invocation for testing and demos.

Run:
    uv run python scripts/forecaster_once.py                    # auto-select highest-velocity concept
    uv run python scripts/forecaster_once.py --concept MCP      # force a specific concept

Exit codes:
    0 — a forecast was produced and persisted
    1 — no candidate concept available (DB doesn't have any mentioned concepts in the lookback window)
    2 — runtime error during forecast
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from fastmcp import Client

from agentradar_core import configure_logging
from agentradar_supervisor.agents import Forecaster


MCP_URL = "http://localhost/mcp/"


async def main_async(concept: str | None) -> int:
    configure_logging()
    agent = Forecaster(concept_name=concept)
    try:
        async with Client(MCP_URL) as mcp:
            summary = await agent.run(mcp)
    except Exception as exc:
        print(f"\nForecaster crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print()
    print("=" * 60)
    print("Forecaster one-off complete")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:25} {v}")

    if summary.get("forecasts_produced", 0) == 0:
        print()
        print("No forecast produced. Possible reasons:")
        print("  - No mentioned concepts in the last 90 days")
        print("  - All concepts have a recent (<14d) forecast")
        print("  - The SLM didn't produce a parseable response (check logs)")
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off Forecaster run.")
    parser.add_argument(
        "--concept", default=None,
        help="Force-forecast this specific concept (default: auto-select)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.concept)))


if __name__ == "__main__":
    main()