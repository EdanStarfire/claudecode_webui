"""Unit tests for McpOneshotConnector (issue #1800).

Mirrors test_shared_mcp_connection.py / test_shared_mcp_endtoend.py's stub patterns for
the happy paths, plus real subprocess spawning where the orphan-process/timeout
guarantees actually need exercising (T1/T3/T5).
"""

import asyncio
import os
import sys
import textwrap
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import Tool

from backend.mcp.oneshot_connection import McpOneshotConnector
from backend.mcp.secret_resolver import SharedSecretResolutionError
from backend.mcp_config_manager import McpServerConfig, McpServerType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector(**kwargs):
    oauth_manager = MagicMock()
    oauth_manager.get_stored_token = AsyncMock(return_value=None)
    vault = MagicMock()
    vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    return McpOneshotConnector(oauth_manager, vault)


def _http_cfg(cfg_id="cfg-1", slug="my-server", url="https://example.com/mcp", **kwargs):
    return McpServerConfig(
        id=cfg_id,
        name="My Server",
        slug=slug,
        type=McpServerType.HTTP,
        url=url,
        **kwargs,
    )


def _stdio_cfg(cfg_id="cfg-stdio", command="mcp-server", **kwargs):
    return McpServerConfig(
        id=cfg_id,
        name="Stdio Server",
        slug="stdio-server",
        type=McpServerType.STDIO,
        command=command,
        **kwargs,
    )


def _fake_session(tools=None, initialize_side_effect=None, list_tools_side_effect=None):
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.initialize = AsyncMock(side_effect=initialize_side_effect)
    list_result = MagicMock()
    list_result.tools = tools or []
    sess.list_tools = AsyncMock(return_value=list_result, side_effect=list_tools_side_effect)
    return sess


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


async def _wait_until_pid_gone(pid: int, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while _is_pid_alive(pid):
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"pid {pid} still alive after {timeout}s")
        await asyncio.sleep(0.05)


# A real trivial MCP STDIO server: writes its own PID to a file, then serves one
# "ping" tool over stdio until the client disconnects.
_STUB_STDIO_SERVER_SCRIPT = textwrap.dedent("""
    import sys, os
    from pathlib import Path

    Path(sys.argv[1]).write_text(str(os.getpid()))

    import anyio
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions
    from mcp.types import ServerCapabilities, ToolsCapability, Tool

    server = Server("stub-stdio")

    @server.list_tools()
    async def _list():
        return [Tool(name="ping", description="ping tool", inputSchema={"type": "object"})]

    async def main():
        init_options = InitializationOptions(
            server_name="stub-stdio",
            server_version="0.0.1",
            capabilities=ServerCapabilities(tools=ToolsCapability()),
        )
        async with stdio_server() as (read, write):
            await server.run(read, write, init_options)

    anyio.run(main)
""")

# A process that writes its PID then hangs forever without speaking the MCP protocol —
# used to exercise the timeout + cleanup path.
_HANG_SCRIPT = textwrap.dedent("""
    import sys, os, time
    from pathlib import Path

    Path(sys.argv[1]).write_text(str(os.getpid()))
    time.sleep(9999)
""")


@pytest.fixture
def pid_file(tmp_path):
    return tmp_path / "pid.txt"


def _read_pid(pid_file: Path, timeout: float = 5.0) -> int:
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            return int(pid_file.read_text().strip())
        _time.sleep(0.05)
    raise AssertionError("pid file was never written")


# ---------------------------------------------------------------------------
# T1 — working STDIO (real subprocess), incl. secret-ref env var; no orphan process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_real_subprocess_returns_tools_and_leaves_no_orphan(pid_file, tmp_path):
    script_path = tmp_path / "stub_server.py"
    script_path.write_text(_STUB_STDIO_SERVER_SCRIPT)

    connector = _make_connector()
    connector._vault.resolve_secrets_for_assignment = AsyncMock(
        return_value=[{"name": "FOO", "value": "bar"}]
    )
    cfg = _stdio_cfg(
        command=sys.executable,
        args=[str(script_path), str(pid_file)],
        env={"MY_VAR": "${secret:FOO}"},
    )

    result = await connector.test_connect(cfg)

    assert result["status"] == "connected"
    assert result["stage"] is None
    assert [t.name for t in result["tools"]] == ["ping"]

    pid = _read_pid(pid_file)
    await _wait_until_pid_gone(pid)


@pytest.mark.asyncio
async def test_stdio_broken_command_tags_transport_stage_and_leaves_no_process():
    connector = _make_connector()
    cfg = _stdio_cfg(command="/nonexistent/binary-issue-1800-does-not-exist")

    result = await connector.test_connect(cfg)

    assert result["status"] == "failed"
    assert result["stage"] == "transport"
    assert result["error"]
    assert result["tools"] == []


# ---------------------------------------------------------------------------
# T2 — working HTTP/SSE, incl. OAuth Bearer header injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_oauth_bearer_header_injected_and_tools_returned():
    connector = _make_connector()
    cfg = _http_cfg(oauth_enabled=True)
    token = MagicMock()
    token.access_token = "tok123"
    connector._oauth_manager.get_stored_token = AsyncMock(return_value=token)

    captured_headers = {}

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        captured_headers.update(headers or {})
        yield (MagicMock(), MagicMock(), lambda: None)

    sess = _fake_session(tools=[Tool(name="ping", description="pong", inputSchema={"type": "object"})])

    with (
        patch("backend.mcp.oneshot_connection.ClientSession", return_value=sess),
        patch("backend.mcp.oneshot_connection.streamablehttp_client", fake_http),
    ):
        result = await connector.test_connect(cfg)

    assert captured_headers.get("Authorization") == "Bearer tok123"
    assert result["status"] == "connected"
    assert result["stage"] is None
    assert [t.name for t in result["tools"]] == ["ping"]


@pytest.mark.asyncio
async def test_sse_no_oauth_header_when_oauth_disabled():
    connector = _make_connector()
    cfg = _http_cfg(cfg_id="cfg-sse", oauth_enabled=False)
    cfg.type = McpServerType.SSE

    captured_headers = {"unset": True}

    @asynccontextmanager
    async def fake_sse(url, headers=None, **kw):
        captured_headers.clear()
        captured_headers.update(headers or {})
        yield (MagicMock(), MagicMock())

    sess = _fake_session(tools=[])

    with (
        patch("backend.mcp.oneshot_connection.ClientSession", return_value=sess),
        patch("backend.mcp.oneshot_connection.sse_client", fake_sse),
    ):
        result = await connector.test_connect(cfg)

    assert "Authorization" not in captured_headers
    assert result["status"] == "connected"


# ---------------------------------------------------------------------------
# T3 — HTTP/SSE handshake (auth) failure tagged distinctly from transport failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_handshake_failure_tagged_distinctly_from_transport_failure():
    connector = _make_connector()
    cfg = _http_cfg()

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        yield (MagicMock(), MagicMock(), lambda: None)

    sess = _fake_session(initialize_side_effect=RuntimeError("401 Unauthorized"))

    with (
        patch("backend.mcp.oneshot_connection.ClientSession", return_value=sess),
        patch("backend.mcp.oneshot_connection.streamablehttp_client", fake_http),
    ):
        result = await connector.test_connect(cfg)

    assert result["status"] == "failed"
    assert result["stage"] == "handshake"
    assert "401" in result["error"]


@pytest.mark.asyncio
async def test_list_tools_failure_tagged_as_list_tools_stage():
    connector = _make_connector()
    cfg = _http_cfg()

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        yield (MagicMock(), MagicMock(), lambda: None)

    sess = _fake_session(list_tools_side_effect=RuntimeError("boom"))

    with (
        patch("backend.mcp.oneshot_connection.ClientSession", return_value=sess),
        patch("backend.mcp.oneshot_connection.streamablehttp_client", fake_http),
    ):
        result = await connector.test_connect(cfg)

    assert result["status"] == "failed"
    assert result["stage"] == "list_tools"
    assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# T5 — timeout (real hung STDIO subprocess): bounded wait + no orphan afterward
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_within_bound_and_kills_hung_process(pid_file, tmp_path):
    script_path = tmp_path / "hang_server.py"
    script_path.write_text(_HANG_SCRIPT)

    connector = _make_connector()
    connector.TIMEOUT_SECONDS = 1.0
    cfg = _stdio_cfg(command=sys.executable, args=[str(script_path), str(pid_file)])

    start = asyncio.get_event_loop().time()
    result = await connector.test_connect(cfg)
    elapsed = asyncio.get_event_loop().time() - start

    assert result["status"] == "failed"
    assert result["stage"] == "handshake"
    assert "Timed out" in result["error"]
    assert elapsed < connector.TIMEOUT_SECONDS + 5.0, "must not hang well past the timeout bound"

    pid = _read_pid(pid_file)
    await _wait_until_pid_gone(pid)


# ---------------------------------------------------------------------------
# T6 — concurrent test_connect() for different configs run in parallel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_different_configs_do_not_serialize():
    connector = _make_connector()
    cfg_a = _http_cfg(cfg_id="cfg-a", slug="server-a")
    cfg_b = _http_cfg(cfg_id="cfg-b", slug="server-b")

    block_a = asyncio.Event()
    entered_a = asyncio.Event()

    async def patched_run(self, cfg, stage_tracker):
        if cfg.id == "cfg-a":
            entered_a.set()
            await block_a.wait()
        return {"status": "connected", "stage": None, "tools": [], "error": None}

    with patch.object(McpOneshotConnector, "_run", patched_run):
        task_a = asyncio.create_task(connector.test_connect(cfg_a))
        await entered_a.wait()

        # cfg-b must be able to proceed even while cfg-a's call is blocked.
        result_b = await asyncio.wait_for(connector.test_connect(cfg_b), timeout=1.0)
        assert result_b["status"] == "connected"

        block_a.set()
        result_a = await asyncio.wait_for(task_a, timeout=1.0)
        assert result_a["status"] == "connected"


@pytest.mark.asyncio
async def test_concurrent_same_config_calls_serialize_via_lock():
    connector = _make_connector()
    cfg = _http_cfg()

    order = []

    async def patched_run(self, cfg, stage_tracker):
        order.append("start")
        await asyncio.sleep(0.05)
        order.append("end")
        return {"status": "connected", "stage": None, "tools": [], "error": None}

    with patch.object(McpOneshotConnector, "_run", patched_run):
        await asyncio.gather(connector.test_connect(cfg), connector.test_connect(cfg))

    assert order == ["start", "end", "start", "end"], (
        "second call must not start its run until the first's has fully finished"
    )


# ---------------------------------------------------------------------------
# Secret-ref resolution failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_secret_ref_surfaces_as_transport_stage_failure():
    connector = _make_connector()
    connector._vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    cfg = _stdio_cfg(command="${secret:MISSING}")

    result = await connector.test_connect(cfg)

    assert result["status"] == "failed"
    assert result["stage"] == "transport"
    assert "MISSING" in result["error"] or "not present in the vault" in result["error"]


@pytest.mark.asyncio
async def test_missing_secret_raises_shared_secret_resolution_error_directly():
    """Confirm the exact exception type surfaced by secret_resolver (used inline above)."""
    connector = _make_connector()
    connector._vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    cfg = _stdio_cfg(command="${secret:MISSING}")

    async with AsyncExitStack() as stack:
        with pytest.raises(SharedSecretResolutionError):
            await connector._enter_transport(cfg, stack)


# ---------------------------------------------------------------------------
# Cleanup failure during AsyncExitStack unwind must not escape as a raw exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1800_cleanup_failure_after_success_returns_failed_not_raises():
    """A successful connect whose transport raises while closing (AsyncExitStack
    unwind, triggered by the `return` inside `async with stack:`) must surface as a
    {"status": "failed"} dict, not propagate an unhandled exception to the caller."""
    connector = _make_connector()
    cfg = _http_cfg()

    @asynccontextmanager
    async def fake_http_raises_on_close(url, headers=None, **kw):
        try:
            yield (MagicMock(), MagicMock(), lambda: None)
        finally:
            raise RuntimeError("cleanup boom")

    sess = _fake_session(tools=[Tool(name="ping", description="pong", inputSchema={"type": "object"})])

    with (
        patch("backend.mcp.oneshot_connection.ClientSession", return_value=sess),
        patch("backend.mcp.oneshot_connection.streamablehttp_client", fake_http_raises_on_close),
    ):
        result = await connector.test_connect(cfg)

    assert result["status"] == "failed"
    assert "cleanup boom" in result["error"]


# ---------------------------------------------------------------------------
# T7 — no persistent state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_persistent_state_after_successful_call():
    connector = _make_connector()
    cfg = _http_cfg()

    @asynccontextmanager
    async def fake_http(url, headers=None, **kw):
        yield (MagicMock(), MagicMock(), lambda: None)

    sess = _fake_session(tools=[Tool(name="ping", description="pong", inputSchema={"type": "object"})])

    with (
        patch("backend.mcp.oneshot_connection.ClientSession", return_value=sess),
        patch("backend.mcp.oneshot_connection.streamablehttp_client", fake_http),
    ):
        await connector.test_connect(cfg)

    assert not hasattr(connector, "_conns"), "McpOneshotConnector must hold no persisted connection dict"
    assert list(connector._locks.keys()) == [cfg.id]
    assert not connector._locks[cfg.id].locked()
    # The config object itself must be untouched.
    assert cfg.url == "https://example.com/mcp"
