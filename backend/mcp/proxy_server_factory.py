"""
proxy_server_factory — Issue #1484

build_proxy_server(cfg, shared_mgr) -> McpSdkServerConfig dict

Creates a fresh in-process mcp.server.Server per WebUI session whose
list_tools and call_tool handlers delegate to a shared upstream session
held by SharedMcpConnectionManager. Wraps the result in the SDK's
McpSdkServerConfig dict so it can be dropped into ClaudeAgentOptions
.mcp_servers without further translation.
"""

from mcp.server import Server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult


def build_proxy_server(cfg, shared_mgr) -> dict:
    """Return a McpSdkServerConfig dict for the SDK's mcp_servers map."""

    # Issue #1982: degrade instead of raise — the SDK calls list_tools() on every
    # attached server at its own connect time, so a failed shared server must not
    # take down the SDK's own connection to this proxy.
    async def _list_tools(ctx, params) -> ListToolsResult:
        return ListToolsResult(tools=await shared_mgr.list_tools_or_cached(cfg))

    # No get_tool_input_schema is passed to Server(...) below: upstream server
    # validates; cached Tool schemas on this proxy may temporarily lag after
    # notifications/tools/list_changed.
    async def _call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
        return await shared_mgr.call_tool(cfg, params.name, params.arguments or {})

    server = Server(
        cfg.slug, version="1.0.0", on_list_tools=_list_tools, on_call_tool=_call_tool
    )

    return {"type": "sdk", "name": cfg.slug, "instance": server}
