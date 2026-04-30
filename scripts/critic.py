"""
One-off Critic run. The supervisor handles automatic scheduling; this is
for manual testing, dry-run inspection, and reproducing specific decisions.

Run:
    uv run python scripts/critic.py                # process all pending
    uv run python scripts/critic.py --limit 5      # process N
    uv run python scripts/critic.py --dry-run      # decide without committing
"""

from __future__ import annotations

import argparse
import asyncio

from fastmcp import Client

from agentradar_core import configure_logging
from agentradar_supervisor.agents import Critic

MCP_URL = "http://localhost/mcp/"


async def main_async(limit: int, dry_run: bool) -> None:
    configure_logging()
    agent = Critic(batch_limit=limit, dry_run=dry_run)
    async with Client(MCP_URL) as mcp:
        summary = await agent.run(mcp)
    print()
    print("=" * 60)
    print("Critic one-off complete")
    print("=" * 60)
    for k, v in summary.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for sk, sv in v.items():
                print(f"    {sk:20} {sv}")
        else:
            print(f"  {k:25} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off Critic run.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args.limit, args.dry_run))


if __name__ == "__main__":
    main()