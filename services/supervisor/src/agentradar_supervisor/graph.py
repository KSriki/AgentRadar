"""
ROMA recursive orchestrator, expressed as a LangGraph StateGraph.

Pattern: Atomizer → Planner → Executor → Aggregator, with conditional
edges routing based on each node's output. Atomic tasks short-circuit
the Planner. Composite tasks (Session 2+) decompose into subtasks that
the Executor recursively invokes through the same graph.

Why LangGraph for this specifically:
- State persistence between nodes via the typed state schema
- Conditional routing as declarative data, not buried in Python ifs
- Future: checkpointer for resumability if a long forecast crashes mid-run
- Future: LangSmith integration gives visual traces of which path ran

For workflows that AREN'T graph-shaped (the supervisor's keep-alive loop,
the Scouts' linear pipelines), we deliberately use plain Python/asyncio
instead. ROMA earns its keep on the Forecaster, not the scheduler.
"""

from __future__ import annotations

from agentradar_core import get_logger
from langgraph.graph import END, START, StateGraph

from agentradar_supervisor.nodes import (
    aggregate,
    atomize,
    execute,
    plan,
    route_after_atomize,
    route_after_plan,
)
from agentradar_supervisor.state import ForecastState

log = get_logger(__name__)


def build_roma_graph() -> Any:
    """
    Compile the ROMA StateGraph.

    Topology:
        START → atomize → (atomic? → execute → aggregate → END)
                       → (composite? → plan → execute → aggregate → END)

    Note: in Session 1, the composite path's planner is a no-op that returns
    empty subtasks; the planner→aggregator edge handles this gracefully.
    Session 2 will populate the planner properly.
    """
    graph = StateGraph(ForecastState)

    graph.add_node("atomize", atomize)
    graph.add_node("plan", plan)
    graph.add_node("execute", execute)
    graph.add_node("aggregate", aggregate)

    graph.add_edge(START, "atomize")

    graph.add_conditional_edges(
        "atomize",
        route_after_atomize,
        {
            "execute": "execute",
            "plan": "plan",
        },
    )

    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {
            "execute": "execute",
            "aggregate": "aggregate",
        },
    )

    graph.add_edge("execute", "aggregate")
    graph.add_edge("aggregate", END)

    compiled = graph.compile()
    log.info("roma.graph.compiled")
    return compiled


# Module-level singleton — compile once at import, reuse across invocations
_compiled_graph = None


def get_roma_graph():
    """Lazy-singleton accessor for the compiled ROMA graph."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_roma_graph()
    return _compiled_graph


# Type-only import to avoid circular reference at module load
from typing import Any  # noqa: E402
