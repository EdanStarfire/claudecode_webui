"""End-to-end REMOTE-relay tests for Batch 2's wholesale-relay routers (issue #498).

Uses an in-process ASGI fixture standing up a second ClaudeWebUI in headless mode as
a faithful stand-in for REMOTE (per PLAN_498.md §4.3) — the Hub's RemoteBackend talks
to it over a real httpx.AsyncClient wired to an ASGITransport instead of a socket, so
this exercises the actual mirrored routes end-to-end, not a hand-mocked transport.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI

REMOTE_TOKEN = "remote-test-token"


def _make_remote(tmp_path):
    """A headless ClaudeWebUI instance standing in for REMOTE."""
    return ClaudeWebUI(
        data_dir=tmp_path / "remote_data",
        backend_mode="headless",
        backend_auth_token=REMOTE_TOKEN,
    )


def _wire_hub_to_remote(hub: ClaudeWebUI, remote: ClaudeWebUI) -> None:
    """Point the Hub's coordinator at `remote` via ASGITransport instead of a socket."""
    backend = RemoteBackend("http://remote.test", REMOTE_TOKEN, hub.session_queues)
    backend._client = AsyncClient(
        base_url="http://remote.test/api/backend",
        headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
        transport=ASGITransport(app=remote.app),
    )
    hub.coordinator.backend = backend
    hub.coordinator.backend_mode = BackendMode.REMOTE


@pytest.mark.asyncio
async def test_diff_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    _wire_hub_to_remote(hub, remote)

    ctx = {"exists": True, "working_directory": "/tmp/nonexistent-repo-498"}
    with patch.object(remote.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/sessions/s1/diff")

    assert response.status_code == 200
    # Not a git repo (path doesn't exist) — but the point is REMOTE answered, not the Hub.
    assert response.json()["is_git_repo"] is False


@pytest.mark.asyncio
async def test_diff_not_relayed_in_local_mode(tmp_path):
    """Sanity check: LOCAL mode never touches the relay — same assertion shape, no REMOTE wiring."""
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_local")
    ctx = {"exists": True, "working_directory": "/tmp/nonexistent-repo-498"}
    with patch.object(hub.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/sessions/s1/diff")
    assert response.status_code == 200
    assert response.json()["is_git_repo"] is False


@pytest.mark.asyncio
async def test_edit_history_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data2")
    _wire_hub_to_remote(hub, remote)

    ctx = {"exists": True, "working_directory": "/tmp/nonexistent-repo-498"}
    with patch.object(remote.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        with patch.object(remote.service, "get_session_messages_path", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
                response = await client.get("/api/sessions/s1/edit-history")

    assert response.status_code == 200
    assert response.json() == {"entries": [], "tool_count": 0}


@pytest.mark.asyncio
async def test_archives_history_status_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data3")
    _wire_hub_to_remote(hub, remote)

    expected = {"has_history": False, "has_archives": False}
    with patch.object(
        remote.coordinator, "check_history_archives", new_callable=AsyncMock, return_value=expected
    ):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/sessions/s1/history-archives-status")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_relay_returns_502_when_remote_unreachable(tmp_path):
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data4")
    # RemoteBackend pointed at a real (nonexistent) address — no ASGI wiring —
    # so the relay call genuinely fails to connect.
    backend = RemoteBackend("http://127.0.0.1:1", "tok", hub.session_queues)
    hub.coordinator.backend = backend
    hub.coordinator.backend_mode = BackendMode.REMOTE

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/sessions/s1/diff")

    assert response.status_code == 502
