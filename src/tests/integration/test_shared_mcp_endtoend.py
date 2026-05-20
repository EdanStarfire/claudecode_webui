"""End-to-end integration test for SharedMcpConnectionManager (issue #1484 §4.5).

Uses an in-process stub MCP server connected via in-memory streams so no real
network is required. The test verifies that:
  - Two proxy sessions share exactly one upstream ClientSession.initialize() call.
  - Tool calls from both proxies reach the stub and succeed.
  - Secret refs in config headers are resolved to plaintext before the upstream opens.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import (
    CallToolResult,
    ServerCapabilities,
    TextContent,
    Tool,
    ToolsCapability,
)

from src.mcp.proxy_server_factory import build_proxy_server
from src.mcp.shared_connection_manager import SharedMcpConnectionManager, _SharedConn
from src.mcp_config_manager import McpServerConfig, McpServerType

# ---------------------------------------------------------------------------
# Stub upstream server factory
# ---------------------------------------------------------------------------


def _make_stub_server(name: str = "stub-upstream"):
    """Create a minimal stub MCP server that counts tool invocations."""
    server = Server(name)
    counters = {"calls": 0}

    @server.list_tools()
    async def _list():
        return [Tool(name="ping", description="ping tool", inputSchema={"type": "object"})]

    @server.call_tool(validate_input=False)
    async def _call(tool_name, arguments):
        counters["calls"] += 1
        return CallToolResult(
            content=[TextContent(type="text", text=f"pong-{counters['calls']}")],
            isError=False,
        )

    return server, counters


def _make_mgr():
    oauth_manager = MagicMock()
    oauth_manager.get_stored_token = AsyncMock(return_value=None)
    refresh_mgr = MagicMock()
    refresh_mgr.add_broadcast_subscriber = MagicMock()
    vault = MagicMock()
    vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    return SharedMcpConnectionManager(oauth_manager, refresh_mgr, vault)


def _http_cfg(cfg_id="integ-cfg", slug="integ-server", **kwargs):
    return McpServerConfig(
        id=cfg_id,
        name="Integ Server",
        slug=slug,
        type=McpServerType.HTTP,
        url="https://stub.local/mcp",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Helper: build an _enter_transport replacement that uses in-memory streams
# to connect to a stub server running as a background task.
# ---------------------------------------------------------------------------


def _make_in_memory_transport(stub_server: Server):
    """Return an _enter_transport coroutine that connects to stub_server in-memory."""
    init_options = InitializationOptions(
        server_name=stub_server.name,
        server_version="0.0.1",
        capabilities=ServerCapabilities(tools=ToolsCapability()),
    )
    open_count = {"n": 0}

    async def _enter_transport(self_mgr, cfg, stack):
        open_count["n"] += 1
        # Enter the memory-stream context manager into the exit stack so streams
        # stay alive until the connection is closed.
        ctx = create_client_server_memory_streams()
        client_streams, server_streams = await stack.enter_async_context(ctx)
        client_read, client_write = client_streams
        server_read, server_write = server_streams

        # Run the stub server as a background task; cancel it when the stack exits.
        stub_task = asyncio.create_task(
            stub_server.run(server_read, server_write, init_options)
        )
        stack.callback(stub_task.cancel)

        return client_read, client_write

    return _enter_transport, open_count


# ---------------------------------------------------------------------------
# Test 1: one upstream, two sessions, both call a tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_upstream_connection_for_two_proxy_sessions():
    """Load-bearing test: two proxies share exactly one upstream ClientSession.

    Verifies:
    - SharedMcpConnectionManager opens the upstream exactly once (refcount logic).
    - list_tools through the shared manager returns the stub's tools.
    - call_tool through each proxy successfully reaches the stub.
    """
    stub_server, counters = _make_stub_server()
    mgr = _make_mgr()
    cfg = _http_cfg()

    transport_fn, open_count = _make_in_memory_transport(stub_server)

    with patch.object(SharedMcpConnectionManager, "_enter_transport", transport_fn):
        # First session attaches (opens the upstream)
        conn1 = await mgr.get_or_open(cfg)
        assert open_count["n"] == 1, "upstream must open exactly once on first attach"

        # Second session attaches (reuses the upstream — no new open)
        conn2 = await mgr.get_or_open(cfg)
        assert open_count["n"] == 1, "upstream must NOT open again for second session"
        assert conn1 is conn2, "both sessions share the same _SharedConn"
        assert conn1.refcount == 2

        # Build two proxy server dicts (one per session)
        proxy1 = build_proxy_server(cfg, mgr)
        proxy2 = build_proxy_server(cfg, mgr)
        assert proxy1["type"] == "sdk" and proxy2["type"] == "sdk"
        assert proxy1["name"] == cfg.slug and proxy2["name"] == cfg.slug

        # list_tools via the shared manager reflects stub's tools
        tools = await mgr.list_tools(cfg)
        assert len(tools) == 1 and tools[0].name == "ping"

        # call_tool via the manager (both proxies delegate to the same mgr)
        result1 = await mgr.call_tool(cfg, "ping", {})
        result2 = await mgr.call_tool(cfg, "ping", {})

        assert result1.isError is False
        assert result2.isError is False
        assert counters["calls"] == 2, "stub must have received exactly 2 tool calls"
        assert "pong-1" in result1.content[0].text
        assert "pong-2" in result2.content[0].text

        # Cleanup: release both session refcounts
        await mgr.release(cfg.id)
        await mgr.release(cfg.id)

    await mgr.shutdown()


# ---------------------------------------------------------------------------
# Test 2: secret refs in config headers resolve to plaintext before upstream opens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_endtoend_secret_ref_header():
    """Secret ${secret:X} in config headers resolves to plaintext before transport opens."""
    captured_headers: dict = {}

    oauth_manager = MagicMock()
    oauth_manager.get_stored_token = AsyncMock(return_value=None)
    refresh_mgr = MagicMock()
    refresh_mgr.add_broadcast_subscriber = MagicMock()

    vault = MagicMock()

    async def fake_resolve(names):
        return [{"name": n, "value": "sk-test"} for n in names]

    vault.resolve_secrets_for_assignment = fake_resolve

    mgr = SharedMcpConnectionManager(oauth_manager, refresh_mgr, vault)

    cfg = McpServerConfig(
        id="secret-cfg",
        name="Secret Server",
        slug="secret-server",
        type=McpServerType.HTTP,
        url="https://stub.local/mcp",
        headers={"X-API-Key": "${secret:X_API_KEY}"},
    )

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        captured_headers.update(headers or {})
        yield (MagicMock(), MagicMock(), lambda: None)

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.initialize = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    fake_session.list_tools = AsyncMock(return_value=list_result)

    with (
        patch("src.mcp.shared_connection_manager.streamablehttp_client", fake_http),
        patch("src.mcp.shared_connection_manager.ClientSession", return_value=fake_session),
    ):
        conn = _SharedConn(cfg_id=cfg.id)
        await mgr._open_locked(cfg, conn)

    assert captured_headers.get("X-API-Key") == "sk-test", (
        "Plaintext secret value must be sent to the upstream transport"
    )
    assert "${secret:" not in str(captured_headers), (
        "Raw placeholder must NEVER appear in outbound headers"
    )
