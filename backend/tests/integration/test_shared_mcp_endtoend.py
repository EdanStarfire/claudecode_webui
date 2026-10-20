"""End-to-end integration test for SharedMcpConnectionManager (issue #1484 §4.5).

Uses an in-process stub MCP server connected via in-memory streams so no real
network is required. The test verifies that:
  - Two proxy sessions share exactly one upstream ClientSession.initialize() call.
  - Tool calls from both proxies reach the stub and succeed.
  - Secret refs in config headers are resolved to plaintext before the upstream opens.
  - Issue #2234: resources/prompts round-trip through the real upstream
    Server/ClientSession pair (not just mocked unit tests), proving the
    wire-level JSON-RPC shapes actually match.
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
    CompleteResult,
    Completion,
    CompletionArgument,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    Prompt,
    PromptMessage,
    PromptReference,
    ReadResourceResult,
    Resource,
    ServerCapabilities,
    TextContent,
    TextResourceContents,
    Tool,
    ToolsCapability,
)

from backend.mcp.proxy_server_factory import build_proxy_server
from backend.mcp.shared_connection_manager import SharedMcpConnectionManager, _SharedConn
from backend.mcp_config_manager import McpServerConfig, McpServerType

# ---------------------------------------------------------------------------
# Stub upstream server factory
# ---------------------------------------------------------------------------


def _make_stub_server(name: str = "stub-upstream"):
    """Create a minimal stub MCP server that counts tool invocations."""
    counters = {"calls": 0}

    async def _on_list_tools(ctx, params):
        return ListToolsResult(
            tools=[Tool(name="ping", description="ping tool", inputSchema={"type": "object"})]
        )

    async def _on_call_tool(ctx, params):
        counters["calls"] += 1
        return CallToolResult(
            content=[TextContent(type="text", text=f"pong-{counters['calls']}")],
            isError=False,
        )

    server = Server(name, on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)
    return server, counters


def _make_stub_server_with_resources_and_prompts(name: str = "stub-upstream-resources"):
    """Issue #2234: a stub server that additionally implements resources/prompts,
    for an end-to-end round trip through the real upstream Server/ClientSession
    pair (not mocked unit tests)."""

    async def _on_list_tools(ctx, params):
        # SharedMcpConnectionManager._run_connection calls session.list_tools()
        # unconditionally at connect time (same as every other shared config);
        # a server with no tools still needs a handler registered to answer it.
        return ListToolsResult(tools=[])

    async def _on_list_resources(ctx, params):
        return ListResourcesResult(resources=[Resource(name="r1", uri="mem:///r1.txt")])

    async def _on_read_resource(ctx, params):
        return ReadResourceResult(
            contents=[TextResourceContents(uri=params.uri, text="resource body")]
        )

    async def _on_list_prompts(ctx, params):
        return ListPromptsResult(prompts=[Prompt(name="greet", description="greet prompt")])

    async def _on_get_prompt(ctx, params):
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=f"prompt:{params.name}"),
                )
            ]
        )

    async def _on_completion(ctx, params):
        # Echo argument.name/.value back so the test can prove the manager's
        # complete() forwarded the real CompletionArgument-derived dict
        # correctly through the real ClientSession.complete() wire call
        # (ClientSession.complete() reconstructs CompletionArgument(**argument)
        # server-side via the JSON-RPC round trip — a wrong dict shape here
        # would raise a pydantic ValidationError inside the real session).
        return CompleteResult(
            completion=Completion(values=[f"{params.argument.name}={params.argument.value}"])
        )

    server = Server(
        name,
        on_list_tools=_on_list_tools,
        on_list_resources=_on_list_resources,
        on_read_resource=_on_read_resource,
        on_list_prompts=_on_list_prompts,
        on_get_prompt=_on_get_prompt,
        on_completion=_on_completion,
    )
    return server


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

        assert result1.is_error is False
        assert result2.is_error is False
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
    captured: dict = {}

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
    async def fake_http(url, http_client=None, **kw):
        # mcp 2.x: headers now live on the httpx2.AsyncClient passed as
        # `http_client`, not a `headers` kwarg on streamable_http_client itself.
        # Keep the real Headers object (case-insensitive lookup) rather than
        # a plain dict() copy, which would flatten keys to lowercase.
        if http_client is not None:
            captured["headers"] = http_client.headers
        yield (MagicMock(), MagicMock())

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.initialize = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    fake_session.list_tools = AsyncMock(return_value=list_result)

    with (
        patch("backend.mcp.shared_connection_manager.streamable_http_client", fake_http),
        patch("backend.mcp.shared_connection_manager.ClientSession", return_value=fake_session),
    ):
        conn = _SharedConn(cfg_id=cfg.id)
        await mgr._open_locked(cfg, conn)

    assert captured["headers"].get("X-API-Key") == "sk-test", (
        "Plaintext secret value must be sent to the upstream transport"
    )
    assert "${secret:" not in str(captured["headers"]), (
        "Raw placeholder must NEVER appear in outbound headers"
    )


# ---------------------------------------------------------------------------
# Test 3 (issue #2234): resources/prompts round-trip through a real upstream
# Server/ClientSession pair — not just mocked unit tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resources_and_prompts_round_trip_through_shared_manager():
    """list_resources/read_resource/list_prompts/get_prompt all reach a real
    stub upstream Server over in-memory streams and return real data."""
    stub_server = _make_stub_server_with_resources_and_prompts()
    mgr = _make_mgr()
    cfg = _http_cfg(cfg_id="integ-cfg-resources", slug="integ-server-resources")

    transport_fn, open_count = _make_in_memory_transport(stub_server)

    with patch.object(SharedMcpConnectionManager, "_enter_transport", transport_fn):
        await mgr.get_or_open(cfg)
        assert open_count["n"] == 1

        resources = await mgr.list_resources(cfg)
        assert len(resources) == 1 and resources[0].name == "r1"

        read_result = await mgr.read_resource(cfg, "mem:///r1.txt")
        assert read_result.contents[0].text == "resource body"

        prompts = await mgr.list_prompts(cfg)
        assert len(prompts) == 1 and prompts[0].name == "greet"

        prompt_result = await mgr.get_prompt(cfg, "greet", {"name": "world"})
        assert prompt_result.messages[0].content.text == "prompt:greet"

        # Also exercise the per-session proxy's handlers end-to-end, not just
        # the manager directly — mirrors how build_proxy_server's closures are
        # actually invoked by the SDK.
        proxy = build_proxy_server(cfg, mgr)["instance"]
        list_resources_entry = proxy.get_request_handler("resources/list")
        proxy_resources = await list_resources_entry.handler(MagicMock(), None)
        assert proxy_resources.resources[0].name == "r1"

        # completion/complete round-trips through the real ClientSession.complete()
        # wire call (not a mock) — proves the proxy's argument-dict mapping
        # produces a shape the real session can serialize and the real upstream
        # can deserialize via CompletionArgument(**argument), unlike a mocked
        # session which would mask a dict-shape bug.
        complete_entry = proxy.get_request_handler("completion/complete")
        complete_params = MagicMock()
        complete_params.ref = PromptReference(name="greet")
        complete_params.argument = CompletionArgument(name="city", value="San")
        complete_params.context = None
        complete_result = await complete_entry.handler(MagicMock(), complete_params)
        assert complete_result.completion.values == ["city=San"]

        await mgr.release(cfg.id)

    await mgr.shutdown()


@pytest.mark.asyncio
async def test_resources_list_degrades_to_empty_when_upstream_does_not_implement_it():
    """A shared server that does NOT implement resources/list must degrade to
    an empty list through the shared manager, not crash the session/connection
    (issue #2234 AC: 'empty list... rather than breaking the session's
    connection'). Uses the tools-only stub server from Test 1."""
    stub_server, _counters = _make_stub_server()
    mgr = _make_mgr()
    cfg = _http_cfg(cfg_id="integ-cfg-no-resources", slug="integ-server-no-resources")

    transport_fn, open_count = _make_in_memory_transport(stub_server)

    with patch.object(SharedMcpConnectionManager, "_enter_transport", transport_fn):
        await mgr.get_or_open(cfg)
        assert open_count["n"] == 1

        resources = await mgr.list_resources(cfg)
        assert resources == []

        await mgr.release(cfg.id)

    await mgr.shutdown()
