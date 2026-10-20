"""Unit tests for SharedMcpConnectionManager (issue #1484 §4.1, issue #1505)."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import pytest
from mcp.types import (
    CallToolResult,
    CompleteResult,
    Completion,
    EmptyResult,
    GetPromptResult,
    Prompt,
    PromptReference,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)

from backend.mcp.proxy_server_factory import build_proxy_server
from backend.mcp.secret_resolver import SharedSecretResolutionError
from backend.mcp.shared_connection_manager import (
    SharedMcpConnectionManager,
    _describe_open_failure,
    _SharedConn,
)
from backend.mcp_config_manager import McpServerConfig, McpServerType

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
    list_resources_result = MagicMock()
    list_resources_result.resources = []
    sess.list_resources = AsyncMock(return_value=list_resources_result)
    list_templates_result = MagicMock()
    list_templates_result.resource_templates = []
    sess.list_resource_templates = AsyncMock(return_value=list_templates_result)
    sess.read_resource = AsyncMock(return_value=ReadResourceResult(contents=[]))
    list_prompts_result = MagicMock()
    list_prompts_result.prompts = []
    sess.list_prompts = AsyncMock(return_value=list_prompts_result)
    sess.get_prompt = AsyncMock(return_value=GetPromptResult(messages=[]))
    sess.complete = AsyncMock(
        return_value=CompleteResult(completion=Completion(values=["x"]))
    )
    sess.set_logging_level = AsyncMock(return_value=EmptyResult())
    return sess


@asynccontextmanager
async def _fake_http_transport(read=None, write=None):
    yield (read or MagicMock(), write or MagicMock())


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
# Per-config lock granularity (issue #1803): one broken/slow server's open
# must not block another config's open.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_opens_for_distinct_configs_do_not_serialize():
    """Regression test for the bug: distinct configs must not share one lock.

    Fails against the old manager-wide `_open_lock`: cfg-b's open would block
    on the lock held while cfg-a's open is in flight, and the wait_for below
    would raise TimeoutError.
    """
    mgr = _make_mgr()
    cfg_a = _http_cfg(cfg_id="cfg-a", slug="server-a")
    cfg_b = _http_cfg(cfg_id="cfg-b", slug="server-b")

    block_a = asyncio.Event()

    async def patched_open(self, cfg, conn):
        if cfg.id == "cfg-a":
            await block_a.wait()
        await _stub_open_locked(self, cfg, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", patched_open):
        task_a = asyncio.create_task(mgr.get_or_open(cfg_a))
        await asyncio.sleep(0)  # let task_a enter and block inside its own lock

        conn_b = await asyncio.wait_for(mgr.get_or_open(cfg_b), timeout=1.0)
        assert conn_b.cfg_id == "cfg-b"

        block_a.set()
        conn_a = await asyncio.wait_for(task_a, timeout=1.0)
        assert conn_a.cfg_id == "cfg-a"


@pytest.mark.asyncio
async def test_concurrent_opens_for_same_config_serialize_single_open():
    mgr = _make_mgr()
    cfg = _http_cfg()
    open_calls = []

    async def slow_open(self, c, conn):
        open_calls.append(c.id)
        await asyncio.sleep(0.05)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", slow_open):
        conn1, conn2 = await asyncio.gather(mgr.get_or_open(cfg), mgr.get_or_open(cfg))

    assert len(open_calls) == 1, "_open_locked should run once for concurrent same-config opens"
    assert conn1 is conn2
    assert conn1.refcount == 2


@pytest.mark.asyncio
async def test_list_tools_check_does_not_block_unrelated_session_open():
    """Maps to the issue's T4 scenario (issue #1799 Library "Show tools" path) and UC2.

    Fails against the old manager-wide `_open_lock`: get_or_open(cfg_healthy)
    would block on the same lock as list_tools(cfg_broken)'s in-flight open.
    """
    mgr = _make_mgr()
    cfg_broken = _http_cfg(cfg_id="cfg-broken", slug="broken-server")
    cfg_healthy = _http_cfg(cfg_id="cfg-healthy", slug="healthy-server")

    block_broken = asyncio.Event()

    async def patched_open(self, cfg, conn):
        if cfg.id == "cfg-broken":
            await block_broken.wait()
        await _stub_open_locked(self, cfg, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", patched_open):
        task_broken = asyncio.create_task(mgr.list_tools(cfg_broken))
        await asyncio.sleep(0)  # let task_broken enter and block inside its own lock

        conn_healthy = await asyncio.wait_for(mgr.get_or_open(cfg_healthy), timeout=1.0)
        assert conn_healthy.cfg_id == "cfg-healthy"

        block_broken.set()
        tools = await asyncio.wait_for(task_broken, timeout=1.0)
        assert [t.name for t in tools] == ["t1"]


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
# mark_draining / get_or_open race on a never-opened config (issue #1805)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_draining_never_opened_then_get_or_open_raises():
    """T2: mark_draining() on a config that was never opened must record a
    pending-drain marker; the next get_or_open() must raise before ever
    calling _open_locked (no transport/subprocess opened for a doomed attach).
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    await mgr.mark_draining(cfg.id)
    assert cfg.id not in mgr._conns
    assert cfg.id in mgr._pending_drains

    open_calls = []

    async def counting_open(self, c, conn):
        open_calls.append(c.id)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        with pytest.raises(RuntimeError, match="drained"):
            await mgr.get_or_open(cfg)

    assert open_calls == [], "_open_locked must never be called for a pending-drain config"
    assert cfg.id not in mgr._conns
    # The marker is NOT consumed by a rejected get_or_open() — only
    # clear_pending_drain() (re-enable) may remove it. See
    # test_pending_drain_rejects_all_concurrent_racers for why.
    assert cfg.id in mgr._pending_drains


@pytest.mark.asyncio
async def test_pending_drain_rejects_all_concurrent_racers():
    """Regression: a pending-drain marker must reject every concurrent racer,
    not just the first one to observe it.

    Before this fix, get_or_open() discarded the _pending_drains entry on the
    first match, so a second caller racing the same never-opened, just-drained
    config would find the marker already consumed and open a live connection
    to a config that's supposed to be draining/deleted — leaking a connection
    and violating the issue's AC1 ("no call to _open_locked completing
    unguarded").
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    await mgr.mark_draining(cfg.id)
    assert cfg.id in mgr._pending_drains

    open_calls = []

    async def counting_open(self, c, conn):
        open_calls.append(c.id)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        results = await asyncio.gather(
            mgr.get_or_open(cfg), mgr.get_or_open(cfg), return_exceptions=True
        )

    assert all(isinstance(r, RuntimeError) for r in results), (
        "every concurrent racer must be rejected, not just the first"
    )
    assert open_calls == [], "_open_locked must never be called while the config is draining"
    assert cfg.id not in mgr._conns
    assert cfg.id in mgr._pending_drains  # still present; only clear_pending_drain() removes it


@pytest.mark.asyncio
async def test_mark_draining_serializes_behind_inflight_open():
    """T1: mark_draining() must block until an in-flight first-ever open
    completes, proving it's now serialized via the per-config open-lock
    instead of racing the conn's creation/field mutation while _open_locked
    is running.
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    block_open = asyncio.Event()

    async def blocking_open(self, c, conn):
        await block_open.wait()
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", blocking_open):
        task_open = asyncio.create_task(mgr.get_or_open(cfg))
        await asyncio.sleep(0)  # let task_open enter the lock and start blocking open

        task_drain = asyncio.create_task(mgr.mark_draining(cfg.id))
        await asyncio.sleep(0)
        assert not task_drain.done(), "mark_draining must block behind the in-flight open"

        block_open.set()
        conn = await asyncio.wait_for(task_open, timeout=1.0)
        await asyncio.wait_for(task_drain, timeout=1.0)

    assert conn.draining is True
    # refcount was incremented by the in-flight get_or_open before mark_draining
    # could observe the conn, so it's still present (not closed on 0 refcount).
    assert cfg.id in mgr._conns

    await mgr.release(cfg.id)
    assert cfg.id not in mgr._conns


@pytest.mark.asyncio
async def test_clear_pending_drain_allows_reenable_get_or_open_succeeds():
    """Re-enable regression: disable(never-opened) → clear_pending_drain →
    get_or_open must succeed normally, not be incorrectly rejected."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    await mgr.mark_draining(cfg.id)
    assert cfg.id in mgr._pending_drains

    mgr.clear_pending_drain(cfg.id)
    assert cfg.id not in mgr._pending_drains

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    assert conn.session is not None
    assert cfg.id in mgr._conns


@pytest.mark.asyncio
async def test_clear_pending_drain_noop_for_live_draining_conn():
    """clear_pending_drain() only clears the no-conn-yet marker; it must not
    interfere with a real, actively-draining connection."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr.get_or_open(cfg)

    mgr._conns[cfg.id].draining = True
    mgr.clear_pending_drain(cfg.id)

    assert mgr._conns[cfg.id].draining is True
    with pytest.raises(RuntimeError, match="drained"):
        await mgr.get_or_open(cfg)


# ---------------------------------------------------------------------------
# list_tools / _ensure_open — refcount-neutral for non-lifecycle callers
# (issue #1799: a Library-level tools check must not leak a permanent hold
# that would block a subsequent config delete/drain)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_cold_start_opens_and_nets_refcount_to_zero():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        tools = await mgr.list_tools(cfg)

    assert [t.name for t in tools] == ["t1"]
    assert cfg.id in mgr._conns
    assert mgr._conns[cfg.id].refcount == 0


@pytest.mark.asyncio
async def test_list_tools_after_check_config_can_still_drain_immediately():
    """Regression for issue #1799: repeated Library tools checks must not leak
    refcount and block a subsequent config delete/drain."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr.list_tools(cfg)
        await mgr.list_tools(cfg)  # second call reuses the open connection

    assert mgr._conns[cfg.id].refcount == 0
    await mgr.mark_draining(cfg.id)  # should close immediately, no lingering hold
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

    assert result.is_error is True
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
# Mutual exclusion between _on_token_refreshed()'s reopen and get_or_open()'s
# reopen branch (issue #1806)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_token_refresh_and_get_or_open_reopen_serialize():
    """T1: _on_token_refreshed() and get_or_open() must never call _open_locked
    concurrently for the same conn.

    Before the #1806 fix, _on_token_refreshed() only held conn.reconnect_lock (not
    the per-config open-lock), so it could race with get_or_open()'s reopen branch
    and open two competing connections for the same conn. The fix nests
    reconnect_lock inside the open-lock in _on_token_refreshed(), so the two paths
    must now serialize, with the loser observing the winner's live session and
    skipping its own reopen (AC1).
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    mgr.set_cfg_lookup(AsyncMock(return_value=cfg))

    in_flight = 0
    max_in_flight = 0
    entries = []
    entered_gate = asyncio.Event()
    gate = asyncio.Event()

    async def gated_open(self, c, con):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        entries.append(asyncio.current_task().get_name())
        entered_gate.set()
        await gate.wait()
        in_flight -= 1
        await _stub_open_locked(self, c, con)

    with patch.object(SharedMcpConnectionManager, "_open_locked", gated_open):
        t_refresh = asyncio.create_task(mgr._on_token_refreshed(cfg.id), name="refresh")
        await asyncio.wait_for(entered_gate.wait(), timeout=1.0)

        assert max_in_flight == 1
        assert entries == ["refresh"]

        # get_or_open must block on the same open-lock refresh is holding — it
        # cannot enter _open_locked (gated_open) until refresh releases it.
        t_open = asyncio.create_task(mgr.get_or_open(cfg), name="reopen")
        await asyncio.sleep(0)

        assert max_in_flight == 1, "only one _open_locked call should be in flight"
        assert entries == ["refresh"], (
            "get_or_open must not enter _open_locked while refresh holds the open-lock"
        )

        gate.set()
        await asyncio.wait_for(asyncio.gather(t_refresh, t_open), timeout=1.0)

    assert max_in_flight == 1, "the two reopen paths must never call _open_locked concurrently"
    assert entries == ["refresh"], "get_or_open should see the refreshed live session and skip its own reopen"
    assert conn.session is not None
    assert conn.refcount == 2


@pytest.mark.asyncio
async def test_repeated_concurrent_refresh_and_reopen_no_deadlock():
    """T4: stress the nested open-lock/reconnect_lock acquisition in
    _on_token_refreshed() across many concurrent iterations to prove no deadlock.
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    mgr.set_cfg_lookup(AsyncMock(return_value=cfg))

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        for i in range(20):
            prev_generation = conn.generation
            refresh_coro = mgr._on_token_refreshed(cfg.id)
            open_coro = mgr.get_or_open(cfg)
            if i % 2 == 0:
                tasks = [asyncio.create_task(refresh_coro), asyncio.create_task(open_coro)]
            else:
                tasks = [asyncio.create_task(open_coro), asyncio.create_task(refresh_coro)]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)
            except TimeoutError:
                pytest.fail(f"iteration {i}: concurrent refresh/reopen deadlocked")
            assert conn.generation >= prev_generation, "generation must not go backwards"

    assert conn.generation > 0


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

    captured = {}

    @asynccontextmanager
    async def fake_http(url, http_client=None, **kw):
        # mcp 2.x: headers now live on the httpx2.AsyncClient passed as
        # `http_client`, not a `headers` kwarg on streamable_http_client itself.
        # Keep the real Headers object (case-insensitive lookup) rather than
        # a plain dict() copy, which would flatten keys to lowercase.
        if http_client is not None:
            captured["headers"] = http_client.headers
        yield (MagicMock(), MagicMock())

    with (
        patch("backend.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("backend.mcp.shared_connection_manager.streamable_http_client", fake_http),
    ):
        sess = _fake_session()
        mock_session_cls.return_value = sess
        try:
            await mgr._open_locked(cfg, _SharedConn(cfg_id=cfg.id))
        except Exception:
            pass

    assert captured["headers"].get("Authorization") == "Bearer tok123"


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

    captured = {}

    @asynccontextmanager
    async def fake_http(url, http_client=None, **kw):
        # mcp 2.x: headers now live on the httpx2.AsyncClient passed as
        # `http_client`, not a `headers` kwarg on streamable_http_client itself.
        # Keep the real Headers object (case-insensitive lookup) rather than
        # a plain dict() copy, which would flatten keys to lowercase.
        if http_client is not None:
            captured["headers"] = http_client.headers
        yield (MagicMock(), MagicMock())

    with (
        patch("backend.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("backend.mcp.shared_connection_manager.streamable_http_client", fake_http),
    ):
        sess = _fake_session()
        mock_session_cls.return_value = sess
        conn = _SharedConn(cfg_id=cfg.id)
        await mgr._open_locked(cfg, conn)

    assert captured["headers"].get("X-API-Key") == "sk-real"
    assert "${secret:KEY}" not in str(captured["headers"])


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
        yield (MagicMock(), MagicMock())

    with (
        patch("backend.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("backend.mcp.shared_connection_manager.streamable_http_client", fake_http),
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
        patch("backend.mcp.shared_connection_manager.ClientSession") as mock_session_cls,
        patch("backend.mcp.shared_connection_manager.stdio_client", fake_stdio),
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
        yield (MagicMock(), MagicMock())

    with patch("backend.mcp.shared_connection_manager.streamable_http_client", fake_http):
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

    assert result.is_error is True
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
    assert result.is_error is True
    assert conn.session is None

    # Second call should trigger reopen via get_or_open's elif branch
    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        result2 = await mgr.call_tool(cfg, "t1", {})

    assert len(open_calls) == 2
    assert result2.is_error is False


@pytest.mark.asyncio
async def test_broken_resource_error_also_recovers():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    conn.session.call_tool = AsyncMock(side_effect=anyio.BrokenResourceError())

    result = await mgr.call_tool(cfg, "t1", {})

    assert result.is_error is True
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
        patch("backend.mcp.shared_connection_manager.ClientSession", return_value=_fake_session()),
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
    from backend.mcp.shared_connection_manager import _disconnect_error_result_named

    result = _disconnect_error_result_named(cfg.id, reason="boom")
    assert result.is_error is True
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
        patch("backend.mcp.shared_connection_manager.ClientSession", return_value=_fake_session()),
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


# ---------------------------------------------------------------------------
# _describe_open_failure — ExceptionGroup unwrapping (issue #1982)
# ---------------------------------------------------------------------------


def test_describe_open_failure_unwraps_nested_exception_group():
    inner = ValueError("root cause")
    group = ExceptionGroup("outer", [ExceptionGroup("inner", [inner])])

    assert _describe_open_failure(group) == "ValueError: root cause"


def test_describe_open_failure_unwraps_base_exception_group():
    inner = OSError("network down")
    group = BaseExceptionGroup("outer", [inner])

    assert _describe_open_failure(group) == "OSError: network down"


def test_describe_open_failure_plain_exception():
    exc = ConnectionRefusedError("refused")

    assert _describe_open_failure(exc) == "ConnectionRefusedError: refused"


def test_describe_open_failure_http_status_error():
    request = httpx.Request("GET", "https://example.com/mcp")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("401", request=request, response=response)

    assert _describe_open_failure(exc) == "401 Unauthorized"


def test_describe_open_failure_http_status_error_wrapped_in_group():
    """Mirrors how anyio actually raises transport failures: wrapped in a TaskGroup."""
    request = httpx.Request("GET", "https://example.com/mcp")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("401", request=request, response=response)
    group = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [exc])

    assert _describe_open_failure(group) == "401 Unauthorized"


def test_describe_open_failure_cached_failure_returned_verbatim():
    """A cooldown fast-fail's exception must re-describe to the identical text,
    not a doubled/prefixed version — e.g. never "RuntimeError: 401 Unauthorized"
    when a caller re-describes the exception _open_locked's cooldown branch raised."""
    from backend.mcp.shared_connection_manager import _CachedOpenError

    cached = _CachedOpenError("401 Unauthorized")

    assert _describe_open_failure(cached) == "401 Unauthorized"


# ---------------------------------------------------------------------------
# Open failure records a human-readable reason on the connection (issue #1982)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_failure_records_human_readable_401_reason():
    """get_or_open() still raises for direct callers (unchanged contract), but
    conn.last_open_error is set to a human-readable 401 message, not the opaque
    ExceptionGroup string.
    """
    mgr = _make_mgr()
    cfg = _http_cfg()

    request = httpx.Request("GET", "https://example.com/mcp")
    response = httpx.Response(401, request=request)
    http_err = httpx.HTTPStatusError("401", request=request, response=response)
    group = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [http_err])

    async def failing_enter_transport(self_mgr, c, stack):
        raise group

    with patch.object(SharedMcpConnectionManager, "_enter_transport", failing_enter_transport):
        with pytest.raises(ExceptionGroup):
            await mgr.get_or_open(cfg)

    conn = mgr._conns[cfg.id]
    assert conn.last_open_error == "401 Unauthorized"
    assert conn.session is None


# ---------------------------------------------------------------------------
# list_tools() degrades instead of raising on a never-successful connection
# (issue #1982)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_still_raises_for_direct_callers():
    """list_tools() itself must keep raising — application_service.get_mcp_config_tools()
    (the Library "Show tools" diagnostic) depends on this to report status="failed"
    with a real error message instead of a misleading "connected, 0 tools"."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    async def failing_open(self_mgr, c, conn):
        raise RuntimeError("boom")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        with pytest.raises(RuntimeError, match="boom"):
            await mgr.list_tools(cfg)


@pytest.mark.asyncio
async def test_list_tools_or_cached_returns_empty_list_when_open_attempt_failed():
    """list_tools_or_cached() — used by the in-process SDK proxy — must degrade
    instead of raising, since the SDK calls list_tools() on every attached server
    at its own connect time."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    async def failing_open(self_mgr, c, conn):
        raise RuntimeError("boom")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        tools = await mgr.list_tools_or_cached(cfg)

    assert tools == []


# ---------------------------------------------------------------------------
# Open-retry cooldown — no reconnect storm against a still-down host (issue #1982)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_retry_cooldown_skips_transport_after_recent_failure():
    mgr = _make_mgr()
    cfg = _http_cfg()
    enter_calls = []

    async def failing_enter_transport(self_mgr, c, stack):
        enter_calls.append(c.id)
        raise RuntimeError("connection refused")

    with patch.object(SharedMcpConnectionManager, "_enter_transport", failing_enter_transport):
        with pytest.raises(Exception, match="connection refused"):
            await mgr.get_or_open(cfg)

        # Second attempt lands within OPEN_RETRY_COOLDOWN_SECONDS of the first
        # failure — must fail fast on the cached reason, not re-enter transport.
        with pytest.raises(RuntimeError, match="connection refused"):
            await mgr.get_or_open(cfg)

    assert len(enter_calls) == 1, "cooldown must prevent a second real transport attempt"


@pytest.mark.asyncio
async def test_open_retry_cooldown_reason_not_double_wrapped():
    """A caller that re-describes the cooldown fast-fail's exception (e.g.
    SessionCoordinator._get_mcp_sdk_config()) must see the identical clean reason
    as the original failure — not "RuntimeError: 401 Unauthorized"."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    request = httpx.Request("GET", "https://example.com/mcp")
    response = httpx.Response(401, request=request)
    http_err = httpx.HTTPStatusError("401", request=request, response=response)
    group = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [http_err])

    async def failing_enter_transport(self_mgr, c, stack):
        raise group

    with patch.object(SharedMcpConnectionManager, "_enter_transport", failing_enter_transport):
        with pytest.raises(ExceptionGroup):
            await mgr.get_or_open(cfg)

        try:
            await mgr.get_or_open(cfg)
            pytest.fail("expected the cooldown fast-fail to raise")
        except Exception as cooldown_exc:
            assert _describe_open_failure(cooldown_exc) == "401 Unauthorized"


# ---------------------------------------------------------------------------
# Token refresh must reconnect a conn that has never successfully opened
# (issue #1982 — regression for the gap in the pre-existing
# test_token_refresh_triggers_reconnect, which only covers "was open, then
# refreshed")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_refresh_reconnects_conn_that_never_opened():
    mgr = _make_mgr()
    cfg = _http_cfg()
    open_calls = []

    async def failing_open(self_mgr, c, conn):
        open_calls.append(c.id)
        raise RuntimeError("still down")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        with pytest.raises(RuntimeError):
            await mgr.get_or_open(cfg)

    assert len(open_calls) == 1
    conn = mgr._conns[cfg.id]
    assert conn.session is None, "precondition: conn exists but has never successfully opened"

    mgr.set_cfg_lookup(AsyncMock(return_value=cfg))

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        await mgr._on_token_refreshed(cfg.id)

    assert conn.session is not None, (
        "token refresh must attempt to open a conn that never succeeded before, "
        "not just one that was open and later dropped"
    )


# ---------------------------------------------------------------------------
# Issue #2004 story 4: mcp 2.x delivers client cancellation to this project's
# proxy Server for the first time (mcp 1.x never did — a slow/stuck call used
# to run to completion even after Claude Code gave up on it). A cancellation
# delivered mid-flight through build_proxy_server()'s on_call_tool handler must
# reach SharedMcpConnectionManager.call_tool() and leave reconnect_lock released,
# not stuck held forever.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_mid_call_releases_reconnect_lock_and_recovers():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    call_started = asyncio.Event()

    async def blocking_call_tool(name, arguments):
        call_started.set()
        await asyncio.sleep(9999)  # stands in for an abandoned/hung upstream call

    conn.session.call_tool = blocking_call_tool

    server_config = build_proxy_server(cfg, mgr)
    entry = server_config["instance"].get_request_handler("tools/call")
    params = MagicMock()
    params.name = "t1"
    params.arguments = {}

    task = asyncio.create_task(entry.handler(MagicMock(), params))
    await asyncio.wait_for(call_started.wait(), timeout=1.0)

    assert conn.reconnect_lock.locked(), (
        "precondition: the lock must actually be held at the point of cancellation, "
        "or releasing it below proves nothing"
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not conn.reconnect_lock.locked(), (
        "a cancelled call_tool must still release reconnect_lock via the "
        "existing try/finally — not leave it held for the next caller"
    )

    # A subsequent call on the same cfg.id must still succeed normally.
    conn.session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text="ok")], isError=False
        )
    )
    result = await mgr.call_tool(cfg, "t1", {})
    assert result.is_error is False


# ---------------------------------------------------------------------------
# Issue #2234: list-shaped calls (resources/prompts) degrade to an empty list
# on failure, mirroring list_tools_or_cached() — never raise.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_resources_delegates_to_session():
    mgr = _make_mgr()
    cfg = _http_cfg()
    resources = [Resource(name="r1", uri="file:///a.txt")]

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.list_resources.return_value.resources = resources

    result = await mgr.list_resources(cfg)

    assert result == resources


@pytest.mark.asyncio
async def test_list_resources_degrades_to_empty_list_on_failure():
    mgr = _make_mgr()
    cfg = _http_cfg()

    async def failing_open(self_mgr, c, conn):
        raise RuntimeError("boom")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        resources = await mgr.list_resources(cfg)

    assert resources == []


@pytest.mark.asyncio
async def test_list_resource_templates_delegates_to_session():
    mgr = _make_mgr()
    cfg = _http_cfg()
    templates = [ResourceTemplate(name="rt1", uriTemplate="file:///{name}.txt")]

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.list_resource_templates.return_value.resource_templates = templates

    result = await mgr.list_resource_templates(cfg)

    assert result == templates


@pytest.mark.asyncio
async def test_list_resource_templates_degrades_to_empty_list_on_failure():
    mgr = _make_mgr()
    cfg = _http_cfg()

    async def failing_open(self_mgr, c, conn):
        raise RuntimeError("boom")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        templates = await mgr.list_resource_templates(cfg)

    assert templates == []


@pytest.mark.asyncio
async def test_list_prompts_delegates_to_session():
    mgr = _make_mgr()
    cfg = _http_cfg()
    prompts = [Prompt(name="p1")]

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.list_prompts.return_value.prompts = prompts

    result = await mgr.list_prompts(cfg)

    assert result == prompts


@pytest.mark.asyncio
async def test_list_prompts_degrades_to_empty_list_on_failure():
    mgr = _make_mgr()
    cfg = _http_cfg()

    async def failing_open(self_mgr, c, conn):
        raise RuntimeError("boom")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        prompts = await mgr.list_prompts(cfg)

    assert prompts == []


@pytest.mark.asyncio
async def test_list_resources_degrades_to_empty_list_when_session_call_itself_fails():
    """Even with a live, already-open session, a failing list call (not just a
    failed open) must still degrade to [] rather than raise."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.list_resources = AsyncMock(side_effect=RuntimeError("not supported"))

    resources = await mgr.list_resources(cfg)

    assert resources == []


# ---------------------------------------------------------------------------
# Issue #2234: single-item calls (read_resource, get_prompt, complete,
# set_logging_level) are explicit caller-triggered actions — raise a
# descriptive exception on disconnect/failure rather than degrading.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_resource_delegates_to_session():
    mgr = _make_mgr()
    cfg = _http_cfg()
    expected = ReadResourceResult(
        contents=[TextResourceContents(uri="file:///a.txt", text="hi")]
    )

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.read_resource = AsyncMock(return_value=expected)

    result = await mgr.read_resource(cfg, "file:///a.txt")

    conn.session.read_resource.assert_awaited_once_with("file:///a.txt")
    assert result == expected


def _fake_ensure_open_returning(conn):
    """_ensure_open() always reopens a session=None conn via get_or_open(), so a
    direct `conn.session = None` assignment doesn't exercise _call_locked's own
    session-None check (reachable only in the narrow race window between
    _ensure_open() returning and reconnect_lock.acquire() succeeding). Patching
    _ensure_open() itself to hand back an already-disconnected conn isolates
    that branch directly.
    """

    async def _fake(self_mgr, cfg):
        return conn

    return _fake


@pytest.mark.asyncio
async def test_read_resource_raises_descriptive_error_when_disconnected():
    mgr = _make_mgr()
    cfg = _http_cfg()
    conn = _SharedConn(cfg_id=cfg.id, session=None, last_open_error="401 Unauthorized")

    with patch.object(
        SharedMcpConnectionManager, "_ensure_open", _fake_ensure_open_returning(conn)
    ):
        with pytest.raises(RuntimeError, match="401 Unauthorized"):
            await mgr.read_resource(cfg, "file:///a.txt")


@pytest.mark.asyncio
async def test_read_resource_raises_when_reconnect_lock_held_too_long():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)

    mgr.RECONNECT_WAIT_SECONDS = 0.05
    await conn.reconnect_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="disconnected"):
            await mgr.read_resource(cfg, "file:///a.txt")
    finally:
        conn.reconnect_lock.release()


@pytest.mark.asyncio
async def test_read_resource_propagates_upstream_exception():
    """A failure from the upstream call itself (not a disconnect) must
    propagate unchanged, not be swallowed into a stub result."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.read_resource = AsyncMock(side_effect=ValueError("no such resource"))

    with pytest.raises(ValueError, match="no such resource"):
        await mgr.read_resource(cfg, "file:///missing.txt")


@pytest.mark.asyncio
async def test_read_resource_marks_disconnected_on_closed_resource_error():
    """Mirrors test_call_tool_returns_disconnect_on_closed_resource_error:
    _call_locked must mark the conn disconnected (conn.session reset to None)
    on a transport-closed error, same as _call_tool_locked, so the next call
    reopens it instead of repeatedly hitting the same dead session forever."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.read_resource = AsyncMock(side_effect=anyio.ClosedResourceError())

    with pytest.raises(RuntimeError, match="disconnected"):
        await mgr.read_resource(cfg, "file:///a.txt")

    assert conn.session is None


@pytest.mark.asyncio
async def test_next_read_resource_after_closed_resource_error_triggers_reopen():
    mgr = _make_mgr()
    cfg = _http_cfg()
    open_calls = []

    async def counting_open(self, c, conn):
        open_calls.append(c.id)
        await _stub_open_locked(self, c, conn)

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        conn = await mgr.get_or_open(cfg)

    assert len(open_calls) == 1
    conn.session.read_resource = AsyncMock(side_effect=anyio.BrokenResourceError())

    with pytest.raises(RuntimeError, match="disconnected"):
        await mgr.read_resource(cfg, "file:///a.txt")
    assert conn.session is None

    with patch.object(SharedMcpConnectionManager, "_open_locked", counting_open):
        result = await mgr.read_resource(cfg, "file:///a.txt")

    assert len(open_calls) == 2
    assert result == ReadResourceResult(contents=[])


@pytest.mark.asyncio
async def test_get_prompt_delegates_to_session():
    mgr = _make_mgr()
    cfg = _http_cfg()
    expected = GetPromptResult(messages=[])

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.get_prompt = AsyncMock(return_value=expected)

    result = await mgr.get_prompt(cfg, "p1", {"arg": "val"})

    conn.session.get_prompt.assert_awaited_once_with("p1", {"arg": "val"})
    assert result == expected


@pytest.mark.asyncio
async def test_get_prompt_raises_descriptive_error_when_disconnected():
    mgr = _make_mgr()
    cfg = _http_cfg()
    conn = _SharedConn(cfg_id=cfg.id, session=None)

    with patch.object(
        SharedMcpConnectionManager, "_ensure_open", _fake_ensure_open_returning(conn)
    ):
        with pytest.raises(RuntimeError, match="disconnected"):
            await mgr.get_prompt(cfg, "p1")


@pytest.mark.asyncio
async def test_complete_maps_completion_argument_object_to_plain_dict():
    """ClientSession.complete() takes a plain dict for `argument`, but the
    proxy's on_completion handler receives a CompletionArgument object
    (.name/.value) and a CompletionContext|None (.arguments) — the manager's
    complete() itself takes the already-mapped plain dicts (mapping happens
    in build_proxy_server's _complete closure); this test covers the manager
    side: it must forward argument/context_arguments through unchanged."""
    mgr = _make_mgr()
    cfg = _http_cfg()
    expected = CompleteResult(completion=Completion(values=["foo", "foobar"]))

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.complete = AsyncMock(return_value=expected)

    ref = PromptReference(name="p1")
    result = await mgr.complete(cfg, ref, {"name": "fo"}, {"other": "bar"})

    conn.session.complete.assert_awaited_once_with(ref, {"name": "fo"}, {"other": "bar"})
    assert result == expected


@pytest.mark.asyncio
async def test_complete_raises_descriptive_error_when_disconnected():
    mgr = _make_mgr()
    cfg = _http_cfg()
    conn = _SharedConn(cfg_id=cfg.id, session=None)

    with patch.object(
        SharedMcpConnectionManager, "_ensure_open", _fake_ensure_open_returning(conn)
    ):
        with pytest.raises(RuntimeError, match="disconnected"):
            await mgr.complete(cfg, PromptReference(name="p1"), {"name": "fo"})


@pytest.mark.asyncio
async def test_set_logging_level_delegates_to_session():
    mgr = _make_mgr()
    cfg = _http_cfg()

    with patch.object(SharedMcpConnectionManager, "_open_locked", _stub_open_locked):
        conn = await mgr.get_or_open(cfg)
    conn.session.set_logging_level = AsyncMock(return_value=EmptyResult())

    result = await mgr.set_logging_level(cfg, "debug")

    conn.session.set_logging_level.assert_awaited_once_with("debug")
    assert isinstance(result, EmptyResult)


@pytest.mark.asyncio
async def test_set_logging_level_raises_descriptive_error_when_disconnected():
    mgr = _make_mgr()
    cfg = _http_cfg()
    conn = _SharedConn(cfg_id=cfg.id, session=None)

    with patch.object(
        SharedMcpConnectionManager, "_ensure_open", _fake_ensure_open_returning(conn)
    ):
        with pytest.raises(RuntimeError, match="disconnected"):
            await mgr.set_logging_level(cfg, "debug")


@pytest.mark.asyncio
async def test_single_item_call_raises_when_open_attempt_itself_fails():
    """If _ensure_open's underlying open attempt raises (never-successful
    connection, e.g. a cold-started cfg with a down host), the raw exception
    must be converted into the same descriptive RuntimeError shape, not left
    as an opaque ExceptionGroup."""
    mgr = _make_mgr()
    cfg = _http_cfg()

    async def failing_open(self_mgr, c, conn):
        raise RuntimeError("connection refused")

    with patch.object(SharedMcpConnectionManager, "_open_locked", failing_open):
        with pytest.raises(RuntimeError, match="disconnected"):
            await mgr.read_resource(cfg, "file:///a.txt")
