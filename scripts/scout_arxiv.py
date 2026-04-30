"""
One-off arXiv Scout invocation. The supervisor handles automatic scheduling;
this script is for manual testing, demos, and reproducing specific runs.

Run:
    uv run python scripts/scout_arxiv.py
    uv run python scripts/scout_arxiv.py --category cs.LG --max 25
"""

from __future__ import annotations

import argparse
import asyncio

from fastmcp import Client

from agentradar_core import configure_logging
from agentradar_supervisor.agents import ScoutArxiv

MCP_URL = "http://localhost/mcp/"


async def main_async(category: str, max_papers: int) -> None:
    configure_logging()
    agent = ScoutArxiv(category=category, max_papers=max_papers)
    async with Client(MCP_URL) as mcp:
        summary = await agent.run(mcp)
    print()
    print("=" * 60)
    print(f"Scout one-off complete (category={category})")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:25} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off arXiv Scout run.")
    parser.add_argument("--category", default="cs.AI")
    parser.add_argument("--max", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(main_async(args.category, args.max))


if __name__ == "__main__":
    main()