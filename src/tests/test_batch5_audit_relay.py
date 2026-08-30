"""REMOTE-relay tests for Batch 5 (audit.py) — issue #498."""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from ..event_queue import EventQueue
from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI

REMOTE_TOKEN = "remote-test-token"


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
async def test_audit_events_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    _wire_hub_to_remote(hub, remote)

    expected = {"events": [], "next_cursor": None}
    with patch(
        "src.analytics.audit_query.AuditQueryService.query_events",
        new=AsyncMock(return_value=expected),
    ):
        remote.analytics_db._initialized = True
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/audit/events")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_audit_turns_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data2")
    _wire_hub_to_remote(hub, remote)

    expected = {"turns": [], "next_cursor": None}
    with patch(
        "src.analytics.audit_query.AuditQueryService.query_turns",
        new=AsyncMock(return_value=expected),
    ):
        remote.analytics_db._initialized = True
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/audit/turns")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_poll_audit_reads_local_buffer_not_per_request_relay(tmp_path):
    """Pattern B, not Pattern A: /api/poll/audit must read from the Hub's local
    audit_queue rather than opening a fresh relay call per request — otherwise N
    simultaneous browser tabs/consumers would each open an independent long-poll
    through to REMOTE. Seed the local queue directly (bypassing the background
    relay task entirely) and confirm the route serves from it."""
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data3")
    _wire_hub_to_remote(hub, remote)

    hub.audit_queue.append({"type": "audit_event", "data": {"x": 1}})

    # No mock/patch on REMOTE's own AuditQueryService at all — if the route were
    # still relaying per-request, this call would 503 (REMOTE's analytics_db is
    # never initialized in this test) instead of returning the seeded event.
    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/poll/audit", params={"cursor": 0, "timeout": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["data"]["x"] == 1


@pytest.mark.asyncio
async def test_start_audit_relay_buffers_remote_events_into_local_queue(tmp_path):
    """The background relay task itself: long-polls REMOTE's /poll/audit and
    appends each returned event into the Hub-local audit_queue."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(
                200,
                json={"events": [{"type": "audit_event", "data": {"seq": 1}}], "next_cursor": 100.0},
            )
        return httpx.Response(200, json={"events": [], "next_cursor": 100.0})

    backend = RemoteBackend("http://remote.test", "tok", {})
    backend._client = httpx.AsyncClient(
        base_url="http://remote.test/api/backend", transport=httpx.MockTransport(handler)
    )

    queue = EventQueue()
    await backend.start_audit_relay(queue)
    try:
        for _ in range(50):
            if queue.current_cursor > 0:
                break
            await asyncio.sleep(0.01)
        events, cursor = queue.events_since(0)
        assert cursor == 1
        assert events == [{"type": "audit_event", "data": {"seq": 1}}]
    finally:
        await backend.aclose()


@pytest.mark.asyncio
async def test_start_audit_relay_is_idempotent(tmp_path):
    """Calling start_audit_relay twice must not spawn a second background task —
    exactly one shared upstream connection regardless of how many times it's
    invoked (e.g. multiple initialize() calls in a test harness)."""
    backend = RemoteBackend("http://remote.test", "tok", {})
    backend._client = httpx.AsyncClient(
        base_url="http://remote.test/api/backend",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"events": [], "next_cursor": 0})),
    )
    queue = EventQueue()
    await backend.start_audit_relay(queue)
    first_task = backend._audit_relay_task
    await backend.start_audit_relay(queue)
    assert backend._audit_relay_task is first_task
    await backend.aclose()


@pytest.mark.asyncio
async def test_audit_events_local_mode_unaffected(tmp_path):
    """Sanity: LOCAL mode still returns 503 when analytics_db isn't initialized —
    same as before the relay branch was added, proving the branch didn't leak into
    the LOCAL path."""
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data4")
    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/audit/events")
    assert response.status_code == 503
