"""Unit tests for proxy_server_factory (issue #1484 §4.2)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server import Server
from mcp.types import CallToolRequest, CallToolResult, ListToolsRequest, TextContent, Tool

from src.mcp.proxy_server_factory import build_proxy_server
from src.mcp_config_manager import McpServerConfig, McpServerType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cfg(slug="my-mcp"):
    return McpServerConfig(
        id="cfg-1",
        name="My MCP",
        slug=slug,
        type=McpServerType.HTTP,
        url="https://example.com/mcp",
    )


def _shared_mgr(tools=None, call_result=None):
    mgr = MagicMock()
    mgr.list_tools = AsyncMock(
        return_value=tools
        or [Tool(name="echo", description="echo tool", inputSchema={"type": "object"})]
    )
    mgr.call_tool = AsyncMock(
        return_value=call_result
        or CallToolResult(
            content=[TextContent(type="text", text="pong")], isError=False
        )
    )
    return mgr


# ---------------------------------------------------------------------------
# Shape assertions
# ---------------------------------------------------------------------------


def test_build_proxy_server_returns_sdk_config_dict():
    cfg = _cfg()
    mgr = _shared_mgr()
    result = build_proxy_server(cfg, mgr)

    assert result["type"] == "sdk"
    assert result["name"] == cfg.slug
    assert isinstance(result["instance"], Server)


# ---------------------------------------------------------------------------
# Handler delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_handler_delegates_to_shared_manager():
    cfg = _cfg()
    tools = [Tool(name="search", description="search", inputSchema={"type": "object"})]
    mgr = _shared_mgr(tools=tools)
    result = build_proxy_server(cfg, mgr)
    server: Server = result["instance"]

    # Invoke the registered handler directly
    handler = server.request_handlers[ListToolsRequest]
    req = MagicMock()
    req.params = None
    response = await handler(req)

    mgr.list_tools.assert_awaited_once_with(cfg)
    # Handler wraps result in ServerResult; unwrap via .root
    assert response.root.tools == tools


@pytest.mark.asyncio
async def test_call_tool_handler_delegates_and_returns_call_tool_result():
    cfg = _cfg()
    expected = CallToolResult(
        content=[TextContent(type="text", text="hello")], isError=False
    )
    mgr = _shared_mgr(call_result=expected)
    result = build_proxy_server(cfg, mgr)
    server: Server = result["instance"]

    handler = server.request_handlers[CallToolRequest]
    req = MagicMock()
    req.params = MagicMock()
    req.params.name = "echo"
    req.params.arguments = {"msg": "hi"}
    response = await handler(req)

    mgr.call_tool.assert_awaited_once_with(cfg, "echo", {"msg": "hi"})
    assert response.root == expected


# ---------------------------------------------------------------------------
# Each call to build_proxy_server creates a NEW Server instance
# ---------------------------------------------------------------------------


def test_build_proxy_server_creates_fresh_server_each_call():
    cfg = _cfg()
    mgr = _shared_mgr()
    s1 = build_proxy_server(cfg, mgr)["instance"]
    s2 = build_proxy_server(cfg, mgr)["instance"]
    assert s1 is not s2
