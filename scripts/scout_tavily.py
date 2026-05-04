"""
One-off Tavily Scout invocation for manual testing and demos.

Run:
    uv run python scripts/scout_tavily.py
    uv run python scripts/scout_tavily.py --query "agent payment protocols 2026"
"""

from __future__ import annotations

import argparse
import asyncio

from fastmcp import Client

from agentradar_core import configure_logging
from agentradar_supervisor.agents import TavilyScout

MCP_URL = "http://localhost/mcp/"


async def main_async(query: str, max_results: int) -> None:
    configure_logging()
    agent = TavilyScout(query=query, max_results=max_results)
    async with Client(MCP_URL) as mcp:
        summary = await agent.run(mcp)
    print()
    print("=" * 60)
    print(f"Tavily Scout one-off complete (query={query!r})")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:25} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off Tavily Scout run.")
    parser.add_argument(
        "--query", default="new agentic AI protocols 2026",
        help="Search query for Tavily",
    )
    parser.add_argument("--max", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(main_async(args.query, args.max))


if __name__ == "__main__":
    main()