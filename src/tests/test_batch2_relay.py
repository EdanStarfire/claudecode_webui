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


@pytest.mark.asyncio
async def test_list_projects_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data5")
    _wire_hub_to_remote(hub, remote)

    expected = {"projects": [], "total": 0}
    with patch.object(remote.service, "list_projects", new_callable=AsyncMock, return_value=expected):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/projects")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_fleet_halt_all_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data6")
    _wire_hub_to_remote(hub, remote)

    with patch.object(remote.service, "validate_project_exists", new_callable=AsyncMock, return_value=False):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.post("/api/legions/nope/halt-all")

    # REMOTE answered (404 Project not found) rather than the Hub 404ing on its
    # own local project_manager — proves the request actually crossed the relay.
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_session_round_trip_after_relayed_project_creation(tmp_path):
    """Regression test for a real bug the live two-process smoke test caught that
    every other test in this file missed: they all patch validate_project_exists/
    list_projects directly rather than exercising the actual "create a project via
    the relayed endpoint, then create a session under it" round trip. That gap let
    session creation ship completely broken in REMOTE mode — POST /api/sessions
    always 404'd "Project not found" because both validate_project_exists and
    SessionCoordinator.create_session's own project lookup only ever checked the
    Hub's local project_manager, which projects.py's wholesale relay never
    populates in REMOTE mode. Nothing here is mocked: real project creation, real
    project-existence validation, real session creation, all crossing the relay.
    """
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_session_roundtrip")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        project_resp = await client.post(
            "/api/projects", json={"name": "roundtrip-project", "working_directory": str(tmp_path)}
        )
        assert project_resp.status_code == 200, project_resp.text
        project_id = project_resp.json()["project"]["project_id"]

        # The project must be real on REMOTE, not a Hub-local artifact.
        assert await hub.coordinator.project_manager.get_project(project_id) is None
        assert await remote.coordinator.project_manager.get_project(project_id) is not None

        session_resp = await client.post(
            "/api/sessions", json={"project_id": project_id, "session_id": "roundtrip-session-1"}
        )

    assert session_resp.status_code == 200, session_resp.text
    assert session_resp.json()["session_id"] == "roundtrip-session-1"

    # Hub-local bookkeeping still happened (Batch 1's design: the Hub keeps its own
    # SessionManager/EventQueue state for every session regardless of backend mode).
    hub_info = await hub.coordinator.session_manager.get_session_info("roundtrip-session-1")
    assert hub_info is not None
    assert "roundtrip-session-1" in hub.session_queues

    # REMOTE also has a mirrored record — required for the subsequent start_session
    # REMOTE branch's POST /sessions/{id}/start to find anything at all.
    remote_info = await remote.coordinator.session_manager.get_session_info("roundtrip-session-1")
    assert remote_info is not None


@pytest.mark.asyncio
async def test_create_session_404s_for_project_missing_on_both_sides(tmp_path):
    """Sanity check: a genuinely nonexistent project_id still 404s in REMOTE mode
    (proves project_exists's REMOTE branch checks REMOTE for real, rather than the
    fix degenerating into "always assume the project exists")."""
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_session_roundtrip_404")
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.post(
            "/api/sessions", json={"project_id": "does-not-exist-anywhere"}
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_filesystem_browse_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data7")
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/filesystem/browse", params={"path": str(tmp_path)})

    assert response.status_code == 200
    assert response.json()["current_path"] == str(tmp_path)


@pytest.mark.asyncio
async def test_permissions_preview_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data8")
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.post(
            "/api/permissions/preview", json={"working_directory": str(tmp_path)}
        )

    assert response.status_code == 200
    assert "permissions" in response.json()


@pytest.mark.asyncio
async def test_docker_status_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data9")
    _wire_hub_to_remote(hub, remote)

    expected = {"available": False, "images": []}
    with patch("src.docker_utils.check_docker_available", new=AsyncMock(return_value=expected)):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/system/docker-status")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_upload_file_relays_multipart_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data10")
    _wire_hub_to_remote(hub, remote)

    with patch.object(remote.service, "get_session_exists", new_callable=AsyncMock, return_value=False):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.post(
                "/api/sessions/s1/files",
                files={"file": ("hello.txt", b"hello world", "text/plain")},
            )

    # REMOTE answered (404 Session not found) — proves the multipart body actually
    # crossed the relay rather than being consumed/emptied before forwarding.
    assert response.status_code == 404
