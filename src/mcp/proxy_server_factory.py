"""
proxy_server_factory — Issue #1484

build_proxy_server(cfg, shared_mgr) -> McpSdkServerConfig dict

Creates a fresh in-process mcp.server.Server per WebUI session whose
list_tools and call_tool handlers delegate to a shared upstream session
held by SharedMcpConnectionManager. Wraps the result in the SDK's
McpSdkServerConfig dict so it can be dropped into ClaudeAgentOptions
.mcp_servers without further translation.
"""

from typing import Any

from mcp.server import Server
from mcp.types import CallToolResult, Tool


def build_proxy_server(cfg, shared_mgr) -> dict:
    """Return a McpSdkServerConfig dict for the SDK's mcp_servers map."""
    server = Server(cfg.slug, version="1.0.0")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return await shared_mgr.list_tools(cfg)

    # validate_input=False: upstream server validates; cached Tool schemas on this proxy
    # may temporarily lag after notifications/tools/list_changed.
    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        return await shared_mgr.call_tool(cfg, name, arguments)

    return {"type": "sdk", "name": cfg.slug, "instance": server}
