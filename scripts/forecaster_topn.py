"""
One-off forecast.top_n composite workflow for testing and demos.

    uv run python scripts/forecaster_topn.py --n 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from fastmcp import Client

from agentradar_core import configure_logging
from agentradar_supervisor.agents import Forecaster


MCP_URL = "http://localhost/mcp/"


async def main_async(top_n: int) -> int:
    configure_logging()
    agent = Forecaster()
    try:
        async with Client(MCP_URL) as mcp:
            summary = await agent.run_topn(mcp, top_n=top_n)
    except Exception as exc:
        print(f"\nrun_topn crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print()
    print("=" * 60)
    print(f"forecast.top_n complete (n={top_n})")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:25} {v}")
    return 0 if summary.get("forecasts_produced", 0) > 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off top-N forecast.")
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.n)))


if __name__ == "__main__":
    main()