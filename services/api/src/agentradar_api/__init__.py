"""AgentRadar API service — FastAPI app + mounted MCP server."""

from agentradar_api.main import app, main
from agentradar_api.mcp_tools import mcp

__all__ = ["app", "main", "mcp"]