"""Unit tests for RemoteBackend (issue #498) against a mocked httpx transport."""

import asyncio
import json

import httpx
import pytest

from ..event_queue import EventQueue
from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI


def _backend_with_handler(handler, session_queues=None):
    backend = RemoteBackend("http://remote.example", "tok", session_queues or {})
    backend._client = httpx.AsyncClient(
        base_url="http://remote.example/api/backend",
        transport=httpx.MockTransport(handler),
    )
    return backend


@pytest.mark.asyncio
async def test_send_message_relays_and_reports_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    backend = RemoteBackend("http://remote.example", "tok", {})
    backend._client = httpx.AsyncClient(
        base_url="http://remote.example/api/backend",
        headers={"Authorization": "Bearer tok"},
        transport=httpx.MockTransport(handler),
    )

    result = await backend.send_message("s1", "hello")
    assert result is True
    assert seen["path"] == "/api/backend/sessions/s1/messages"
    assert seen["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_send_message_failure_status_returns_false():
    backend = _backend_with_handler(lambda r: httpx.Response(400))
    assert await backend.send_message("s1", "hello") is False


@pytest.mark.asyncio
async def test_send_message_connection_error_returns_false():
    def handler(request):
        raise httpx.ConnectError("unreachable")

    backend = _backend_with_handler(handler)
    assert await backend.send_message("s1", "hello") is False


@pytest.mark.asyncio
async def test_get_mcp_status_defaults_on_failure():
    backend = _backend_with_handler(lambda r: httpx.Response(500))
    assert await backend.get_mcp_status("s1") == {"servers": []}


@pytest.mark.asyncio
async def test_get_mcp_status_success():
    backend = _backend_with_handler(lambda r: httpx.Response(200, json={"servers": ["x"]}))
    assert await backend.get_mcp_status("s1") == {"servers": ["x"]}


@pytest.mark.asyncio
async def test_is_session_active_and_active_session_ids():
    backend = _backend_with_handler(lambda r: httpx.Response(200))
    assert await backend.is_session_active("s1") is False
    backend._active_session_ids.add("s1")
    assert await backend.is_session_active("s1") is True
    assert backend.active_session_ids() == ["s1"]


@pytest.mark.asyncio
async def test_start_session_failure_does_not_spawn_relay_task():
    backend = _backend_with_handler(lambda r: httpx.Response(500, text="boom"))
    started, error = await backend.start_session("s1")
    assert started is False
    assert "500" in error
    assert "s1" not in backend._relay_tasks


@pytest.mark.asyncio
async def test_start_session_connection_failure_reports_unreachable():
    def handler(request):
        raise httpx.ConnectError("down")

    backend = _backend_with_handler(handler)
    started, error = await backend.start_session("s1")
    assert started is False
    assert "unreachable" in error.lower()


@pytest.mark.asyncio
async def test_start_session_success_spawns_relay_loop_that_forwards_events():
    queue = EventQueue()
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backend/sessions/s1/start":
            return httpx.Response(200)
        if request.url.path == "/api/backend/poll/session/s1":
            # Regression check: routers/poll.py's poll_session() reads a "since"
            # query param, not "cursor" — sending the wrong name would be silently
            # ignored by FastAPI and re-fetch the full backlog every iteration.
            assert "since" in request.url.params
            assert "cursor" not in request.url.params
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return httpx.Response(
                    200, json={"events": [{"type": "message", "data": {"x": 1}}], "next_cursor": 1}
                )
            # Subsequent polls: no new events, just idle.
            return httpx.Response(200, json={"events": [], "next_cursor": 1})

        raise AssertionError(f"unexpected path {request.url.path}")

    backend = _backend_with_handler(handler, session_queues={"s1": queue})

    started, error = await backend.start_session("s1")
    assert started is True
    assert error is None
    assert "s1" in backend._relay_tasks

    # Give the background relay task a moment to run its first poll iteration.
    for _ in range(50):
        if queue.current_cursor > 0:
            break
        await asyncio.sleep(0.01)

    events, cursor = queue.events_since(0)
    assert cursor == 1
    assert events == [{"type": "message", "data": {"x": 1}}]

    await backend.aclose()


@pytest.mark.asyncio
async def test_restart_resumes_relay_from_last_cursor_instead_of_refetching_backlog():
    """Regression test: restarting a session (disconnect_session then start_session
    again, exactly what SessionCoordinator.restart_session does) must resume the
    relay poll loop from where it left off, not re-fetch REMOTE's entire event
    backlog from since=0 — the latter duplicates every past message in the Hub's
    local EventQueue (and therefore the frontend) on every restart."""
    queue = EventQueue()
    seen_since_values = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/backend/sessions/s1/start", "/api/backend/sessions/s1/disconnect"):
            return httpx.Response(200)
        if request.url.path == "/api/backend/poll/session/s1":
            since = int(request.url.params["since"])
            seen_since_values.append(since)
            if since == 0:
                return httpx.Response(
                    200, json={"events": [{"type": "message", "data": {"x": 1}}], "next_cursor": 3}
                )
            # Any poll after the first should keep resuming from cursor 3 onward —
            # no new events queued in this test, just confirming "since" never
            # drops back to 0 on the second relay loop.
            return httpx.Response(200, json={"events": [], "next_cursor": 3})

        raise AssertionError(f"unexpected path {request.url.path}")

    backend = _backend_with_handler(handler, session_queues={"s1": queue})

    started, _ = await backend.start_session("s1")
    assert started is True
    for _ in range(50):
        if 0 in seen_since_values:
            break
        await asyncio.sleep(0.01)
    assert 0 in seen_since_values

    # Restart: disconnect (tears down the relay task) then start again — the exact
    # sequence SessionCoordinator.restart_session() performs.
    await backend.disconnect_session("s1")
    assert "s1" not in backend._relay_tasks
    assert backend._relay_cursors["s1"] == 3

    seen_since_values.clear()
    started, _ = await backend.start_session("s1")
    assert started is True
    for _ in range(50):
        if seen_since_values:
            break
        await asyncio.sleep(0.01)

    assert seen_since_values, "relay loop never polled after restart"
    assert 0 not in seen_since_values, (
        f"restart re-fetched from since=0 instead of resuming from cursor 3: {seen_since_values}"
    )
    assert all(v == 3 for v in seen_since_values)

    await backend.aclose()


@pytest.mark.asyncio
async def test_terminate_clears_relay_cursor():
    """Unlike disconnect (restart), a genuine terminate should drop the cursor —
    nothing will resume this session's relay."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    backend = _backend_with_handler(handler, session_queues={"s1": EventQueue()})
    backend._relay_cursors["s1"] = 7

    await backend.terminate_session("s1")

    assert "s1" not in backend._relay_cursors


@pytest.mark.asyncio
async def test_relay_loop_gives_up_after_max_failures_and_calls_error_callback(monkeypatch):
    # Real backoff (2s, 4s, 8s, 16s, 30s for 5 failures) would make this test slow —
    # lower the failure threshold instead of touching sleep timing, so only the
    # first (2s) backoff is ever hit.
    monkeypatch.setattr("src.remote_backend.MAX_RELAY_FAILURES", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/backend/sessions/s1/start":
            return httpx.Response(200)
        return httpx.Response(500)

    backend = _backend_with_handler(handler, session_queues={"s1": EventQueue()})

    error_calls = []

    async def error_callback(error_type, error):
        error_calls.append((error_type, str(error)))

    started, _ = await backend.start_session("s1", error_callback=error_callback)
    assert started is True

    for _ in range(200):
        if "s1" not in backend._active_session_ids:
            break
        await asyncio.sleep(0)

    assert "s1" not in backend._active_session_ids
    assert error_calls
    assert error_calls[0][0] == "relay_connection_lost"

    await backend.aclose()


@pytest.mark.asyncio
async def test_terminate_session_stops_relay_and_relays_call():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    backend = _backend_with_handler(handler, session_queues={"s1": EventQueue()})
    backend._active_session_ids.add("s1")
    backend._relay_tasks["s1"] = asyncio.get_event_loop().create_task(asyncio.sleep(100))

    result = await backend.terminate_session("s1")
    assert result is True
    assert "s1" not in backend._active_session_ids
    assert "s1" not in backend._relay_tasks
    assert "/api/backend/sessions/s1/terminate" in calls


# ----------------------------------------------------------------------------
# --remote-backend-url / --remote-backend-token CLI wiring (issue #499)
# ----------------------------------------------------------------------------


def test_cli_remote_backend_url_wires_remote_without_config_file(tmp_path):
    """--remote-backend-url alone (no config file involved) results in
    coordinator.backend_mode == REMOTE with the CLI-supplied URL/token."""
    webui = ClaudeWebUI(
        data_dir=tmp_path / "data",
        remote_backend_url="http://cli-remote.example",
        remote_backend_token="cli-token",
    )
    assert webui.coordinator.backend_mode == BackendMode.REMOTE
    assert isinstance(webui.coordinator.backend, RemoteBackend)
    assert webui.coordinator.backend._base_url == "http://cli-remote.example"
    assert webui.coordinator.backend._client.headers["authorization"] == "Bearer cli-token"


def test_cli_remote_backend_url_wins_wholesale_over_config_file(tmp_path):
    """A CLI-supplied --remote-backend-url replaces the config-file URL+token pair
    wholesale, rather than pairing the new URL with a stale config-file token."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backend": {
            "mode": "frontend",
            "remote_base_url": "http://config-file-remote.example",
            "remote_auth_token": "config-file-token",
        },
    }))

    webui = ClaudeWebUI(
        data_dir=tmp_path / "data",
        config_file=config_file,
        remote_backend_url="http://cli-remote.example",
        remote_backend_token="cli-token",
    )
    assert webui.coordinator.backend_mode == BackendMode.REMOTE
    assert webui.coordinator.backend._base_url == "http://cli-remote.example"
    assert webui.coordinator.backend._client.headers["authorization"] == "Bearer cli-token"


def test_cli_remote_backend_url_without_token_does_not_inherit_config_file_token(tmp_path):
    """CLI URL with no CLI token must not silently pair with the config file's token
    for a (potentially different) remote — the pair is replaced wholesale."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backend": {
            "mode": "frontend",
            "remote_base_url": "http://config-file-remote.example",
            "remote_auth_token": "config-file-token",
        },
    }))

    webui = ClaudeWebUI(
        data_dir=tmp_path / "data",
        config_file=config_file,
        remote_backend_url="http://cli-remote.example",
    )
    assert webui.coordinator.backend_mode == BackendMode.REMOTE
    assert webui.coordinator.backend._base_url == "http://cli-remote.example"
    assert webui.coordinator.backend._client.headers["authorization"] == "Bearer "


def test_no_cli_remote_backend_url_falls_back_to_config_file_pair(tmp_path):
    """With no CLI override, the config file's REMOTE pair is used as-is."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backend": {
            "mode": "frontend",
            "remote_base_url": "http://config-file-remote.example",
            "remote_auth_token": "config-file-token",
        },
    }))

    webui = ClaudeWebUI(data_dir=tmp_path / "data", config_file=config_file)
    assert webui.coordinator.backend_mode == BackendMode.REMOTE
    assert webui.coordinator.backend._base_url == "http://config-file-remote.example"
    assert webui.coordinator.backend._client.headers["authorization"] == "Bearer config-file-token"
