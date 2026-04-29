"""
End-to-end MCP demo: walks through the proposer-critic loop and proves
the knowledge store ends up in the expected state.

Run: docker compose up -d && uv run python scripts/mcp_demo.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastmcp import Client


async def demo() -> None:
    # Connect to the running API container's MCP endpoint
    async with Client("http://localhost:8000/mcp") as client:
        # --- 1. Discover tools -----------------------------------------
        tools = await client.list_tools()
        print(f"\n=== Connected. Server exposes {len(tools)} tools ===")
        for t in sorted(tools, key=lambda t: t.name):
            print(f"  • {t.name}")

        # --- 2. Healthcheck --------------------------------------------
        print("\n=== Healthcheck ===")
        result = await client.call_tool("healthcheck", {})
        print(json.dumps(result.data, indent=2))

        # --- 3. Propose a triple ---------------------------------------
        print("\n=== Proposing triple: MCP -INTRODUCED_BY-> Anthropic ===")
        result = await client.call_tool(
            "propose_triple",
            {
                "proposer_agent": "demo-script",
                "subject": "MCP",
                "predicate": "INTRODUCED_BY",
                "object": "Anthropic",
                "source_id": "demo-source-1",
                "confidence": 0.95,
            },
        )
        proposal = result.data
        print(json.dumps(proposal, indent=2))

        # --- 4. Inspect the pending queue ------------------------------
        print("\n=== Pending triples (Critic's view) ===")
        result = await client.call_tool("list_pending_triples", {"limit": 10})
        for t in result.data:
            print(f"  {t['subject']} --[{t['predicate']}]--> {t['object']} "
                  f"(conf={t['confidence']}, by={t['proposer_agent']})")

        # --- 5. Critic approves ----------------------------------------
        print("\n=== Approving the proposal ===")
        result = await client.call_tool(
            "approve_triple", {"triple_id": proposal["triple_id"]}
        )
        print(json.dumps(result.data, indent=2))

        # --- 6. Verify it's in the graph --------------------------------
        print("\n=== Fetching MCP concept from Neo4j ===")
        result = await client.call_tool("get_concept", {"name": "MCP"})
        if not result.data["found"]:
            print("  not found")
        else:
            print(json.dumps(result.data, indent=2, default=str))

        # --- 7. Record some mentions and check velocity ----------------
        print("\n=== Recording 3 mentions over time ===")
        for i in range(3):
            await client.call_tool(
                "record_mention",
                {
                    "concept_name": "MCP",
                    "source_id": f"demo-paper-{i}",
                    "source_type": "arxiv",
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            )
        print("  recorded.")

        result = await client.call_tool(
            "get_mention_velocity", {"concept_name": "MCP", "window_days": 30}
        )
        print("\n=== Mention velocity ===")
        print(json.dumps(result.data, indent=2))


if __name__ == "__main__":
    asyncio.run(demo())