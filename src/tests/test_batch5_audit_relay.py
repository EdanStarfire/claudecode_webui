"""REMOTE-relay tests for Batch 5 (audit.py) — issue #498."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

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
async def test_poll_audit_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data3")
    _wire_hub_to_remote(hub, remote)

    expected = {"events": [{"type": "audit_event", "data": {"x": 1}}], "next_cursor": 1}
    with patch(
        "src.analytics.audit_query.AuditQueryService.query_events",
        new=AsyncMock(return_value=expected),
    ):
        remote.analytics_db._initialized = True
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/poll/audit", params={"timeout": 1})

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_audit_events_local_mode_unaffected(tmp_path):
    """Sanity: LOCAL mode still returns 503 when analytics_db isn't initialized —
    same as before the relay branch was added, proving the branch didn't leak into
    the LOCAL path."""
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data4")
    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/audit/events")
    assert response.status_code == 503
