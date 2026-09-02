"""Regression tests for issue #499's missing /poll/ui relay.

Before this fix, RemoteBackend relayed /poll/session/{id} (per-session) and
/poll/audit (global, single shared connection) but never /poll/ui — the *global*
UI event stream. Anything broadcast into ui_queue (session PAUSED-for-permission
state via _notify_state_change, rate-limit updates, watchdog alerts, SDK-driven
permission-mode changes, Legion comm/schedule notifications) only ever reached
REMOTE's own local ui_queue, which the Hub never polled. Mirrors the existing
audit-relay tests (test_batch5_audit_relay.py) pattern.
"""

import asyncio

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from ..event_queue import EventQueue
from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI

REMOTE_TOKEN = "remote-test-token-499-ui"


def _make_remote(tmp_path):
    return ClaudeWebUI(
        data_dir=tmp_path / "remote_data", backend_mode="headless", backend_auth_token=REMOTE_TOKEN
    )


def _wire_hub_to_remote(hub: ClaudeWebUI, remote: ClaudeWebUI) -> None:
    backend = RemoteBackend("http://remote.test", REMOTE_TOKEN, hub.session_queues)
    backend._client = AsyncClient(
        base_url="http://remote.test/api/backend",
        headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
        transport=ASGITransport(app=remote.app),
    )
    hub.coordinator.backend = backend
    hub.coordinator.backend_mode = BackendMode.REMOTE


@pytest.mark.asyncio
async def test_start_ui_relay_buffers_remote_events_into_local_queue():
    """The background relay task itself: long-polls REMOTE's /poll/ui and appends
    each returned event into the Hub-local ui_queue."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/backend/poll/ui"
        assert "since" in request.url.params
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "events": [{"type": "session_state_change", "data": {"state": "paused"}}],
                    "next_cursor": 1,
                },
            )
        return httpx.Response(200, json={"events": [], "next_cursor": 1})

    backend = RemoteBackend("http://remote.test", "tok", {})
    backend._client = httpx.AsyncClient(
        base_url="http://remote.test/api/backend", transport=httpx.MockTransport(handler)
    )

    queue = EventQueue()
    await backend.start_ui_relay(queue)
    try:
        for _ in range(50):
            if queue.current_cursor > 0:
                break
            await asyncio.sleep(0.01)
        events, cursor = queue.events_since(0)
        assert cursor == 1
        assert events == [{"type": "session_state_change", "data": {"state": "paused"}}]
    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_start_ui_relay_is_idempotent():
    """Calling start_ui_relay twice must not spawn a second background task."""
    backend = RemoteBackend("http://remote.test", "tok", {})
    backend._client = httpx.AsyncClient(
        base_url="http://remote.test/api/backend",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"events": [], "next_cursor": 0})),
    )
    queue = EventQueue()
    await backend.start_ui_relay(queue)
    first_task = backend._ui_relay_task
    await backend.start_ui_relay(queue)
    assert backend._ui_relay_task is first_task
    await backend.aclose()


@pytest.mark.asyncio
async def test_ui_relay_delivers_remote_broadcast_events_end_to_end(tmp_path):
    """Full round trip: an event landing in REMOTE's real ui_queue (e.g. a session
    PAUSED state-change broadcast) must reach the Hub's own ui_queue through the
    relay, not just a MockTransport."""
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    _wire_hub_to_remote(hub, remote)

    remote.ui_queue.append({"type": "session_state_change", "data": {"state": "paused"}})

    await hub.coordinator.backend.start_ui_relay(hub.ui_queue)
    try:
        for _ in range(50):
            if hub.ui_queue.current_cursor > 0:
                break
            await asyncio.sleep(0.01)
        events, cursor = hub.ui_queue.events_since(0)
        assert cursor == 1
        assert events == [{"type": "session_state_change", "data": {"state": "paused"}}]
    finally:
        await hub.coordinator.backend.aclose()


@pytest.mark.asyncio
async def test_initialize_starts_ui_relay_when_backend_mode_remote(tmp_path):
    """ClaudeWebUI.initialize() must start the UI relay whenever session dispatch
    is REMOTE — the same trigger condition as the existing audit relay."""
    hub = ClaudeWebUI(
        data_dir=tmp_path / "hub_data_init",
        remote_backend_url="http://remote.test",
        remote_backend_token="tok",
    )
    hub.coordinator.backend._client = httpx.AsyncClient(
        base_url="http://remote.test/api/backend",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"events": [], "next_cursor": 0})),
    )
    try:
        await hub.initialize()
        assert hub.coordinator.backend._ui_relay_task is not None
        assert not hub.coordinator.backend._ui_relay_task.done()
    finally:
        await hub.cleanup()
