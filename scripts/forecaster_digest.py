"""
One-off forecast.digest composite workflow.

    uv run python scripts/forecaster_digest.py
    uv run python scripts/forecaster_digest.py --label "Demo week"
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from fastmcp import Client

from agentradar_core import configure_logging
from agentradar_supervisor.agents import Forecaster


MCP_URL = "http://localhost/mcp/"


async def main_async(top_n: int, label: str | None) -> int:
    configure_logging()
    agent = Forecaster()
    try:
        async with Client(MCP_URL) as mcp:
            summary = await agent.run_digest(mcp, top_n=top_n, label=label)
    except Exception as exc:
        print(f"\nrun_digest crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print()
    print("=" * 60)
    print("forecast.digest complete")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:25} {v}")
    return 0 if summary.get("digests_produced", 0) > 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off digest forecast.")
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--label", default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.n, args.label)))


if __name__ == "__main__":
    main()