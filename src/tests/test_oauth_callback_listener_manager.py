"""Unit tests for OAuthCallbackListenerManager / OAuthCallbackListener (issue #1789).

Covers the custom-port listener lifecycle (start/stop/rebuild, multi-path-per-port,
bind-failure conversion) and the shared render_oauth_callback() HTML/parsing helper.
uvicorn is fully mocked — these tests never bind a real socket.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_config_manager import McpServerConfig, McpServerType
from src.oauth_callback_listener_manager import (
    OAuthCallbackListenerManager,
    render_oauth_callback,
)


def _config(**kwargs) -> McpServerConfig:
    defaults = dict(
        id="cfg-1",
        name="test-server",
        slug="test-server",
        type=McpServerType.HTTP,
        url="https://example.com/mcp",
        enabled=True,
        shared_connection=True,
    )
    defaults.update(kwargs)
    return McpServerConfig(**defaults)


def _mock_uvicorn(started: bool = True, bind_error: bool = False):
    """Build a mock uvicorn module whose Server.serve() completes immediately.

    started=True simulates a successful bind. bind_error=True simulates a failed bind:
    server.started never flips, and the task finishes with an OSError (as a real failed
    `socket.bind()` would raise) — the caller's wait loop must observe the task finishing
    and surface that failure instead of hanging until the 10s deadline.

    Note: this intentionally does NOT reproduce uvicorn's real sys.exit(1)-on-bind-failure
    (a literal SystemExit) — raising SystemExit inside an asyncio task propagates out of
    the *entire* event loop (asyncio's Task machinery re-raises KeyboardInterrupt/SystemExit
    after recording them), which would take down the test runner's own loop, not just this
    task. The `isinstance(exc, SystemExit)` conversion branch in _launch_server is exercised
    indirectly via test_litellm_proxy_manager.py's equivalent, already-shipped pattern.
    """
    mock_server = MagicMock()
    mock_server.started = started

    async def _serve():
        if bind_error:
            raise OSError("[Errno 98] error while attempting to bind")

    mock_server.serve = _serve
    mock_uvicorn = MagicMock()
    mock_uvicorn.Server.return_value = mock_server
    mock_uvicorn.Config.return_value = MagicMock()
    return mock_uvicorn, mock_server


# ── render_oauth_callback ───────────────────────────────────────────────────


def _request(params: dict):
    req = MagicMock()
    req.query_params = params
    return req


@pytest.mark.asyncio
async def test_render_oauth_callback_success_calls_complete_flow():
    complete_flow = AsyncMock(return_value="server-1")
    resp = await render_oauth_callback(_request({"code": "abc", "state": "xyz"}), complete_flow)
    complete_flow.assert_awaited_once_with("xyz", "abc")
    assert resp.status_code == 200
    assert b"Connected Successfully" in resp.body


@pytest.mark.asyncio
async def test_render_oauth_callback_error_param_short_circuits():
    complete_flow = AsyncMock()
    resp = await render_oauth_callback(
        _request({"error": "access_denied", "error_description": "nope"}), complete_flow
    )
    complete_flow.assert_not_awaited()
    assert resp.status_code == 400
    assert b"nope" in resp.body


@pytest.mark.asyncio
async def test_render_oauth_callback_missing_params():
    complete_flow = AsyncMock()
    resp = await render_oauth_callback(_request({}), complete_flow)
    complete_flow.assert_not_awaited()
    assert resp.status_code == 400
    assert b"Missing Parameters" in resp.body


@pytest.mark.asyncio
async def test_render_oauth_callback_exception_from_complete_flow():
    complete_flow = AsyncMock(side_effect=ValueError("token exchange failed"))
    resp = await render_oauth_callback(_request({"code": "abc", "state": "xyz"}), complete_flow)
    assert resp.status_code == 400
    assert b"token exchange failed" in resp.body


# ── OAuthCallbackListenerManager ────────────────────────────────────────────


@pytest.fixture
def oauth_manager():
    mgr = AsyncMock()
    mgr.complete_flow.return_value = "cfg-1"
    return mgr


@pytest.fixture
def manager(oauth_manager):
    return OAuthCallbackListenerManager(oauth_manager, host="127.0.0.1")


@pytest.mark.asyncio
async def test_complete_and_broadcast_calls_oauth_manager_and_broadcast(manager, oauth_manager):
    broadcast = MagicMock()
    manager.set_broadcast_callback(broadcast)

    result = await manager.complete_and_broadcast("state-1", "code-1")

    oauth_manager.complete_flow.assert_awaited_once_with("state-1", "code-1")
    broadcast.assert_called_once_with("cfg-1")
    assert result == "cfg-1"


@pytest.mark.asyncio
async def test_complete_and_broadcast_without_callback_does_not_raise(manager, oauth_manager):
    result = await manager.complete_and_broadcast("state-1", "code-1")
    assert result == "cfg-1"


@pytest.mark.asyncio
async def test_apply_config_starts_listener_for_custom_port(manager):
    mock_uvicorn, _ = _mock_uvicorn(started=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback")
        await manager.apply_config(cfg)

    assert manager.is_port_active(8765)


@pytest.mark.asyncio
async def test_apply_config_noop_for_default_config(manager):
    """A config with no custom port never triggers a listener."""
    cfg = _config()
    await manager.apply_config(cfg)
    assert manager._listeners == {}


@pytest.mark.asyncio
async def test_two_configs_share_one_listener_via_distinct_paths(manager):
    mock_uvicorn, _ = _mock_uvicorn(started=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg_a = _config(id="cfg-a", oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback-a")
        cfg_b = _config(id="cfg-b", oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback-b")
        await manager.apply_config(cfg_a)
        await manager.apply_config(cfg_b)

    listener = manager._listeners[8765]
    assert listener.paths == frozenset({"/callback-a", "/callback-b"})


@pytest.mark.asyncio
async def test_remove_config_drops_only_that_configs_path(manager):
    mock_uvicorn, _ = _mock_uvicorn(started=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg_a = _config(id="cfg-a", oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback-a")
        cfg_b = _config(id="cfg-b", oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback-b")
        await manager.apply_config(cfg_a)
        await manager.apply_config(cfg_b)

        await manager.remove_config("cfg-a")

        assert manager.is_port_active(8765)
        assert manager._listeners[8765].paths == frozenset({"/callback-b"})

        await manager.remove_config("cfg-b")

    assert not manager.is_port_active(8765)
    assert 8765 not in manager._listeners


@pytest.mark.asyncio
async def test_apply_config_disabled_config_removes_existing_registration(manager):
    mock_uvicorn, _ = _mock_uvicorn(started=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback")
        await manager.apply_config(cfg)
        assert manager.is_port_active(8765)

        disabled = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback", enabled=False)
        await manager.apply_config(disabled)

    assert not manager.is_port_active(8765)


@pytest.mark.asyncio
async def test_bind_failure_surfaces_to_caller(manager):
    mock_uvicorn, _ = _mock_uvicorn(started=False, bind_error=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback")
        with pytest.raises(OSError, match="bind"):
            await manager.apply_config(cfg)


@pytest.mark.asyncio
async def test_bind_failure_does_not_leak_listener_entry(manager):
    """A failed bind on a brand-new port must not leave a dead listener in _listeners —
    otherwise remove_config() for that never-registered config_id is a permanent no-op."""
    mock_uvicorn, _ = _mock_uvicorn(started=False, bind_error=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback")
        with pytest.raises(OSError):
            await manager.apply_config(cfg)

    assert 8765 not in manager._listeners
    assert cfg.id not in manager._registrations


@pytest.mark.asyncio
async def test_real_system_exit_from_bind_failure_converts_to_runtime_error(manager):
    """uvicorn calls sys.exit(1) on a real bind failure. Raising a literal SystemExit inside
    an asyncio task and letting it propagate unguarded would crash the whole event loop
    (asyncio re-raises SystemExit/KeyboardInterrupt out of run_once() specially, unlike any
    other exception) — this must be intercepted and converted before that happens."""
    mock_server = MagicMock()
    mock_server.started = False

    async def _serve_raises_system_exit():
        raise SystemExit(1)

    mock_server.serve = _serve_raises_system_exit
    mock_uvicorn = MagicMock()
    mock_uvicorn.Server.return_value = mock_server
    mock_uvicorn.Config.return_value = MagicMock()

    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback")
        with pytest.raises(RuntimeError, match="8765"):
            await manager.apply_config(cfg)

    assert 8765 not in manager._listeners


@pytest.mark.asyncio
async def test_shutdown_stops_all_listeners(manager):
    mock_uvicorn, mock_server = _mock_uvicorn(started=True)
    with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
        cfg = _config(oauth_custom_callback_port=8765, oauth_custom_callback_path="/callback")
        await manager.apply_config(cfg)
        assert manager.is_port_active(8765)

        await manager.shutdown()

    assert manager._listeners == {}
    assert manager._registrations == {}
