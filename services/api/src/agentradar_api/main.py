"""
AgentRadar API — FastAPI app hosting REST endpoints and the MCP server.

Layout:
    /              health check
    /api/...       REST endpoints (dashboard + admin)
    /mcp           MCP HTTP transport (agents connect here)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from agentradar_core import (
    bind_trace_id,
    clear_trace_context,
    configure_logging,
    get_logger,
    settings,
)
from agentradar_store import (
    get_neo4j_client,
    get_pg_client,
    get_s3_client,
)

from agentradar_api.rest import router as rest_router

# Import the MCP server instance with all tools registered
from agentradar_api.mcp_tools import mcp

configure_logging()
log = get_logger(__name__)


# Build the MCP ASGI app once, BEFORE the FastAPI app, so we can plumb
# its lifespan into the FastAPI lifespan below. path="/" tells fastmcp
# NOT to add its own /mcp prefix on top of where we mount it. Without
# this, the final endpoint would be /mcp/mcp instead of /mcp.
mcp_asgi_app = mcp.http_app(path="/")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    Eager-connect on startup so the process fails fast if the data plane
    isn't reachable. Also wraps the fastmcp lifespan — mounted sub-apps'
    lifespans are NOT auto-invoked by FastAPI, so we have to call it
    manually or the MCP session manager never initializes.
    """
    log.info("api.starting", env=settings.environment)

    n = get_neo4j_client()
    p = get_pg_client()
    s = get_s3_client()
    await n.connect()
    await p.connect()

    # S3 connection is implicit per-request via aioboto3 sessions; just sanity-check.
    healthy = {
        "neo4j": await n.healthcheck(),
        "postgres": await p.healthcheck(),
        "s3": await s.healthcheck(),
    }
    if not all(healthy.values()):
        log.error("api.data_plane_unhealthy", **healthy)
        raise RuntimeError(f"Data plane not fully healthy: {healthy}")
    log.info("api.ready", **healthy)

    # Now run the fastmcp lifespan (initializes its session manager).
    # Tear-down of our resources happens in the inner finally so it runs
    # AFTER fastmcp has cleanly shut down.
    async with mcp_asgi_app.lifespan(_app):
        try:
            yield
        finally:
            log.info("api.shutting_down")
            await p.close()
            await n.close()


# Create the FastAPI app with lifespan management
app = FastAPI(
    title="AgentRadar API",
    description="REST surface and MCP tool server for AgentRadar.",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount the fastmcp ASGI app at /mcp.
# Final endpoint is http://host:port/mcp because mcp_asgi_app was built
# with path="/" — see the comment above its construction.
app.mount("/mcp", mcp_asgi_app)
# Mount REST endpoints under /api/* (consumed by the dashboard)
app.include_router(rest_router)

# --- REST endpoints --------------------------------------------------------


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "service": "agentradar-api",
        "version": "0.1.0",
        "mcp_endpoint": "/mcp",
        "docs": "/docs",
    }


@app.get("/health", tags=["meta"])
async def health() -> dict[str, bool]:
    """Liveness + dependency check. Used by the docker-compose healthcheck."""
    return {
        "neo4j": await get_neo4j_client().healthcheck(),
        "postgres": await get_pg_client().healthcheck(),
        "s3": await get_s3_client().healthcheck(),
    }


# --- Trace ID middleware ---------------------------------------------------


@app.middleware("http")
async def trace_id_middleware(request, call_next):
    """
    Bind a trace_id to the request's logging context. If a client sends
    X-Trace-Id, we honor it; otherwise we generate one. Every log record
    emitted during this request carries the trace_id automatically thanks
    to structlog's contextvars integration.
    """
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    bind_trace_id(trace_id)
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
    finally:
        clear_trace_context()


# --- Entry point -----------------------------------------------------------


def main() -> None:
    """Run with uvicorn. In container, can also be invoked via `uvicorn` directly."""
    uvicorn.run(
        "agentradar_api.main:app",
        host="0.0.0.0",
        port=8000,
        log_config=None,  # let our structlog config handle it
    )


if __name__ == "__main__":
    main()