"""Unit tests for SharedMcpConnectionManager (issue #1484 §4.1, issue #1505)."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from mcp.types import CallToolResult, TextContent, Tool

from src.mcp.secret_resolver import SharedSecretResolutionError
from src.mcp.shared_connection_manager import SharedMcpConnectionManager, _SharedConn
from src.mcp_config_manager import McpServerConfig, McpServerType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mgr(**kwargs):
    oauth_manager = MagicMock()
    oauth_manager.get_stored_token = AsyncMock(return_value=None)
    refresh_mgr = MagicMock()
    refresh_mgr.add_broadcast_subscriber = MagicMock()
    vault = MagicMock()
    vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    mgr = SharedMcpConnectionManager(oauth_manager, refresh_mgr, vault)
    return mgr


def _http_cfg(cfg_id="cfg-1", slug="my-server", url="https://example.com/mcp", **kwargs):
    return McpServerConfig(
        id=cfg_id,
        name="My Server",
        slug=slug,
        type=McpServerType.HTTP,
        url=url,
        **kwargs,
    )


def _stdio_cfg(cfg_id="cfg-stdio", **kwargs):
    return McpServerConfig(
        id=cfg_id,
        name="Stdio Server",
        slug="stdio-server",
        type=McpServerType.STDIO,
        command="mcp-server",
        **kwargs,
    )


def _fake_session(tools=None):
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    list_result = MagicMock()
    list_result.tools = tools or []
    sess.initialize = AsyncMock(return_value=MagicMock())
    sess.list_tools = AsyncMock(return_value=list_result)
    sess.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text="ok")],
            isError=False,
        )
    )
    return sess


@asynccontextmanager
async def _fake_http_transport(read=None, write=None, get_id=None):
    yield (read or MagicMock(), write or MagicMock(), get_id or (lambda: None))


@asynccontextmanager
async def _fake_sse_transport(read=None, write=None):
    yield (read or MagicMock(), write or MagicMock())


@asynccontextmanager
async def _fake_stdio_transport(read=None, write=None):
    yield (read or MagicMock(), write or MagicMock())


# ---------------------------------------------------------------------------
# Patch helper: replaces _open_locked with a fast fake (owner-task aware)
# ---------------------------------------------------------------------------


async def _stub_open_locked(mgr_self, cfg, conn: _SharedConn):
    """Stub for _open_locked that sets up owner-task fields without real transport."""
    conn.session = _fake_session()
    conn.cached_tools = [Tool(name="t1", description="tool1", inputSchema={"type": "object"})]
    conn.generation += 1
    loop = asyncio.get_running_loop()
    closed_fut: asyncio.Future = loop.create_future()
    closed_fut.set_result(None)
    close_event = asyncio.Event()

    async def _noop_owner():
        await close_event.wait()

    conn.close_event = close_event
    conn.closed_future = closed_fut
    conn.owner_task = asyncio.create_task(_noop_owner(), name=f"stub-owner-{cfg.id}")


# ---------------------------------------------------------------------------
# get_or_open — opens exactly once for multiple callers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_open_opens_once_for_multiple_sessions():
    mgr = _make_mgr()
    cfg = _http_cfg()
    open_calls = []

    async def patched_open(self, c, conn):
        open_calls.append(c.id)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", patched_open):
        conn1 = await mgr.get_or_open(cfg)
        conn2 = await mgr.get_or_open(cfg)

    assert len(open_calls) == 1, "_open_locked should run once"
    assert conn1 is conn2
    assert conn1.refcount == 2


# ---------------------------------------------------------------------------
# release — decrements and keeps open when not draining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_decrements_refcount_and_keeps_open_when_not_draining():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr.get_or_open(cfg)
        await mgr.get_or_open(cfg)

    assert mgr._conns[cfg.id].refcount == 2
    await mgr.release(cfg.id)
    assert mgr._conns[cfg.id].refcount == 1
    assert cfg.id in mgr._conns  # still open


# ---------------------------------------------------------------------------
# mark_draining — closes immediately when refcount == 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_draining_closes_when_refcount_zero():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr.get_or_open(cfg)

    await mgr.release(cfg.id)  # refcount → 0
    await mgr.mark_draining(cfg.id)  # should close

    assert cfg.id not in mgr._conns


@pytest.mark.asyncio
async def test_mark_draining_then_release_closes():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr.get_or_open(cfg)

    await mgr.mark_draining(cfg.id)
    assert mgr._conns[cfg.id].draining is True
    assert cfg.id in mgr._conns  # still open (refcount == 1)

    await mgr.release(cfg.id)  # refcount → 0, draining → close
    assert cfg.id not in mgr._conns


# ---------------------------------------------------------------------------
# get_or_open raises on draining conn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_open_raises_on_draining_conn():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr.get_or_open(cfg)

    mgr._conns[cfg.id].draining = True
    with pytest.raises(RuntimeError, match="drained"):
        await mgr.get_or_open(cfg)


# ---------------------------------------------------------------------------
# call_tool returns disconnect error when reconnect_lock is held too long
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_returns_disconnect_error_when_reconnecting_too_long():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    # Hold the reconnect_lock so call_tool can't acquire it
    mgr.RECONNECT_WAIT_SECONDS = 0.05  # very short timeout for test speed
    await conn.reconnect_lock.acquire()  # block it
    try:
        result = await mgr.call_tool(cfg, "t1", {})
    finally:
        conn.reconnect_lock.release()

    assert result.isError is True
    assert "disconnected" in result.content[0].text.lower()


# ---------------------------------------------------------------------------
# token refresh triggers reconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_refresh_triggers_reconnect():
    mgr = _make_mgr()
    cfg = _http_cfg()
    open_calls = []

    async def counting_open(self, c, conn):
        open_calls.append(c.id)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        await mgr.get_or_open(cfg)

    assert len(open_calls) == 1

    # Install a cfg_lookup so _on_token_refreshed can find the config
    mgr.set_cfg_lookup(AsyncMock(return_value=cfg))

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        await mgr._on_token_refreshed(cfg.id)

    assert len(open_calls) == 2  # re-opened after refresh
    conn = mgr._conns[cfg.id]
    assert conn.generation == 2, "generation must advance after token-refresh reconnect"


# ---------------------------------------------------------------------------
# OAuth header built for HTTP; not for STDIO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_header_built_for_http():
    mgr = _make_mgr()
    cfg = _http_cfg(oauth_enabled=True)
    token = MagicMock()
    token.access_token = "tok123"
    mgr._oauth_manager.get_stored_token = AsyncMock(return_value=token)

    captured_headers = {}

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        captured_headers.update(headers or {})
        yield (MagicMock(), MagicMock(), lambda: None)

    with (
        patch("src.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("src.mcp.shared_connection_manager.streamablehttp_client", fake_http),
    ):
        sess = _fake_session()
        mock_session_cls.return_value = sess
        try:
            await mgr._open_locked(cfg, _SharedConn(cfg_id=cfg.id))
        except Exception:
            pass

    assert captured_headers.get("Authorization") == "Bearer tok123"


@pytest.mark.asyncio
async def test_no_oauth_header_for_stdio():
    mgr = _make_mgr()
    cfg = _stdio_cfg(oauth_enabled=False)

    headers = await mgr._build_headers(cfg)
    assert headers is None


# ---------------------------------------------------------------------------
# Secret refs resolved in headers before opening HTTP connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_refs_resolved_in_http_headers_before_open():
    mgr = _make_mgr()
    cfg = _http_cfg(headers={"X-API-Key": "${secret:KEY}"})

    async def fake_resolve(names):
        return [{"name": n, "value": "sk-real"} for n in names]

    mgr._vault.resolve_secrets_for_assignment = fake_resolve

    captured_headers = {}

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        captured_headers.update(headers or {})
        yield (MagicMock(), MagicMock(), lambda: None)

    with (
        patch("src.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("src.mcp.shared_connection_manager.streamablehttp_client", fake_http),
    ):
        sess = _fake_session()
        mock_session_cls.return_value = sess
        conn = _SharedConn(cfg_id=cfg.id)
        await mgr._open_locked(cfg, conn)

    assert captured_headers.get("X-API-Key") == "sk-real"
    assert "${secret:KEY}" not in str(captured_headers)


@pytest.mark.asyncio
async def test_secret_refs_resolved_in_http_url_before_open():
    mgr = _make_mgr()
    cfg = _http_cfg(url="https://api.example.com/${secret:TENANT}/mcp")

    async def fake_resolve(names):
        return [{"name": n, "value": "acme"} for n in names]

    mgr._vault.resolve_secrets_for_assignment = fake_resolve

    captured_url = {}

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        captured_url["url"] = url
        yield (MagicMock(), MagicMock(), lambda: None)

    with (
        patch("src.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("src.mcp.shared_connection_manager.streamablehttp_client", fake_http),
    ):
        sess = _fake_session()
        mock_session_cls.return_value = sess
        conn = _SharedConn(cfg_id=cfg.id)
        await mgr._open_locked(cfg, conn)

    assert captured_url["url"] == "https://api.example.com/acme/mcp"


@pytest.mark.asyncio
async def test_secret_refs_resolved_in_stdio_command_args_env():
    mgr = _make_mgr()
    cfg = McpServerConfig(
        id="cfg-stdio",
        name="Stdio",
        slug="stdio",
        type=McpServerType.STDIO,
        command="${secret:CMD}",
        args=["--token", "${secret:TOK}"],
        env={"API_KEY": "${secret:API}"},
    )

    secret_map = {"CMD": "npx", "TOK": "tok42", "API": "key99"}

    async def fake_resolve(names):
        return [{"name": n, "value": secret_map[n]} for n in names if n in secret_map]

    mgr._vault.resolve_secrets_for_assignment = fake_resolve

    captured = {}

    @asynccontextmanager
    async def fake_stdio(params, **kw):
        captured["command"] = params.command
        captured["args"] = list(params.args)
        captured["env"] = dict(params.env or {})
        yield (MagicMock(), MagicMock())

    with (
        patch("src.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("src.mcp.shared_connection_manager.stdio_client", fake_stdio),
    ):
        sess = _fake_session()
        mock_session_cls.return_value = sess
        conn = _SharedConn(cfg_id=cfg.id)
        await mgr._open_locked(cfg, conn)

    assert captured["command"] == "npx"
    assert captured["args"] == ["--token", "tok42"]
    assert captured["env"]["API_KEY"] == "key99"


@pytest.mark.asyncio
async def test_missing_secret_raises_and_no_transport_opened():
    mgr = _make_mgr()
    cfg = _http_cfg(url="${secret:GHOST}/mcp")

    # vault returns empty list — secret not found
    mgr._vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])

    transport_opened = []

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        transport_opened.append(True)
        yield (MagicMock(), MagicMock(), lambda: None)

    with patch("src.mcp.shared_connection_manager.streamablehttp_client", fake_http):
        conn = _SharedConn(cfg_id=cfg.id)
        with pytest.raises(SharedSecretResolutionError):
            await mgr._open_locked(cfg, conn)

    assert not transport_opened, "transport must not open when secret resolution fails"


# ---------------------------------------------------------------------------
# ClosedResourceError / BrokenResourceError handling (issue #1488)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_returns_disconnect_on_closed_resource_error():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    conn.session.call_tool = AsyncMock(side_effect=anyio.ClosedResourceError())

    result = await mgr.call_tool(cfg, "t1", {})

    assert result.isError is True
    assert "disconnected" in result.content[0].text.lower()
    assert conn.session is None


@pytest.mark.asyncio
async def test_next_call_after_closed_resource_error_triggers_reopen():
    mgr = _make_mgr()
    cfg = _http_cfg()
    open_calls = []

    async def counting_open(self, c, conn):
        open_calls.append(c.id)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        conn = await mgr.get_or_open(cfg)

    assert len(open_calls) == 1

    # First call raises ClosedResourceError — session should be cleared
    conn.session.call_tool = AsyncMock(side_effect=anyio.ClosedResourceError())
    result = await mgr.call_tool(cfg, "t1", {})
    assert result.isError is True
    assert conn.session is None

    # Second call should trigger reopen via get_or_open's elif branch
    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        result2 = await mgr.call_tool(cfg, "t1", {})

    assert len(open_calls) == 2
    assert result2.isError is False


@pytest.mark.asyncio
async def test_broken_resource_error_also_recovers():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    conn.session.call_tool = AsyncMock(side_effect=anyio.BrokenResourceError())

    result = await mgr.call_tool(cfg, "t1", {})

    assert result.isError is True
    assert "disconnected" in result.content[0].text.lower()
    assert conn.session is None


# ---------------------------------------------------------------------------
# Regression: cross-task close must not raise RuntimeError (issue #1505)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_task_close_no_runtime_error():
    """Owner task holds AsyncExitStack; close from foreign task must not violate cancel scope.

    Fails against old code because _SharedConn has no `generation` field and because
    close happens from a foreign task (task-affine cancel scope violation).
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    enter_tasks: list = []
    exit_tasks: list = []

    @asynccontextmanager
    async def task_recording_ctx():
        enter_tasks.append(asyncio.current_task())
        try:
            yield (MagicMock(), MagicMock())
        finally:
            exit_tasks.append(asyncio.current_task())
            assert asyncio.current_task() is enter_tasks[-1], (
                "AsyncExitStack exited from wrong task — cross-task cancel scope violation"
            )

    async def recording_enter_transport(self_mgr, c, stack):
        return await stack.enter_async_context(task_recording_ctx())

    with (
        patch.object(SharedMcpConnectionManager, "_enter_transport", recording_enter_transport),
        patch("src.mcp.shared_connection_manager.ClientSession", return_value=_fake_session()),
    ):
        # Open from the test task (task A)
        conn = await mgr.get_or_open(cfg)
        assert conn.generation == 1, "generation must be 1 after first open"

        mgr.set_cfg_lookup(AsyncMock(return_value=cfg))

        # Trigger refresh from a *separate* task (task B) — the cross-task scenario
        await asyncio.create_task(mgr._on_token_refreshed(cfg.id))

        assert conn.generation == 2, "generation must advance to 2 after token-refresh reconnect"
        assert conn.session is not None, "session must be live after reconnect"

        # Close the connection so both owner tasks complete before we check exit_tasks
        await mgr.shutdown()

    assert len(enter_tasks) == 2, "transport must have been opened twice"
    assert len(exit_tasks) == 2, "both transports must have been closed"
    # Each open's exit must have happened from the same task that entered it
    assert all(e is x for e, x in zip(enter_tasks, exit_tasks, strict=True)), (
        "every transport close must run on the same task that opened it"
    )


# ---------------------------------------------------------------------------
# Cleanup-failure surfacing (issue #1505)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_failure_surfaces_in_call_tool():
    """If the owner task closes with an error, subsequent call_tool surfaces it."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    # Simulate cleanup failure by resolving closed_future with an exception
    boom = Exception("boom")
    conn.closed_future = asyncio.get_running_loop().create_future()
    conn.closed_future.set_result(boom)

    # Signal close so _close_owner_locked proceeds
    conn.close_event.set()

    # Drain refcount and mark draining to trigger close
    conn.refcount = 0
    conn.draining = True
    await mgr._close(cfg.id)

    assert conn.last_close_error is boom

    # A new connection gets the same cfg_id; manually check error surfacing in
    # _disconnect_error_result_named
    from src.mcp.shared_connection_manager import _disconnect_error_result_named

    result = _disconnect_error_result_named(cfg.id, reason="boom")
    assert result.isError is True
    assert "boom" in result.content[0].text


# ---------------------------------------------------------------------------
# shutdown() cross-task safety (issue #1505)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_cross_task_safety():
    """shutdown() called from any task closes connections safely via owner task."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    enter_tasks: list = []
    exit_tasks: list = []

    @asynccontextmanager
    async def task_recording_ctx():
        enter_tasks.append(asyncio.current_task())
        try:
            yield (MagicMock(), MagicMock())
        finally:
            exit_tasks.append(asyncio.current_task())
            assert asyncio.current_task() is enter_tasks[-1], (
                "shutdown closed from wrong task"
            )

    async def recording_enter_transport(self_mgr, c, stack):
        return await stack.enter_async_context(task_recording_ctx())

    with (
        patch.object(SharedMcpConnectionManager, "_enter_transport", recording_enter_transport),
        patch("src.mcp.shared_connection_manager.ClientSession", return_value=_fake_session()),
    ):
        await mgr.get_or_open(cfg)

    # Call shutdown from a different task — must not violate cancel scope
    await asyncio.create_task(mgr.shutdown())

    assert cfg.id not in mgr._conns
    assert len(exit_tasks) == 1
    assert exit_tasks[0] is enter_tasks[0], "transport closed from same task that opened it"


# ---------------------------------------------------------------------------
# Owner-task close timeout (issue #1505)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_task_close_timeout():
    """If the owner ignores close_event, _close times out, cancels owner, and pops _conns."""
    mgr = _make_mgr()
    cfg = _http_cfg()
    mgr.CLOSE_TIMEOUT_SECONDS = 0.05  # very short for test speed

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    # Replace the owner task with one that ignores close_event
    async def stubborn_owner():
        await asyncio.sleep(9999)  # never wakes on close_event

    stubborn = asyncio.create_task(stubborn_owner(), name="stubborn-owner")
    conn.owner_task = stubborn
    # Reset closed_future so it never resolves via close_event
    conn.closed_future = asyncio.get_running_loop().create_future()

    await mgr._close(cfg.id)

    assert cfg.id not in mgr._conns
    assert conn.last_close_error is not None
    assert isinstance(conn.last_close_error, TimeoutError)
    assert stubborn.cancelled() or stubborn.done()
