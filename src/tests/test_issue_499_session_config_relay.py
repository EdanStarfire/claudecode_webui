"""Regression tests for issue #499's session-config relay fix.

Before this fix, `PATCH /api/sessions/{id}` (and the sibling name/permission-mode/
model endpoints) only ever mutated the Hub's own local `session_manager` — never
relayed to a REMOTE backend. A session dispatched to REMOTE would silently only ever
run with whatever config it got at creation time; any later edit (model, tools,
extra_env credentials, MCP servers, ...) would appear to succeed on the Hub while
REMOTE's actual persisted config silently diverged. This is exactly the gap that left
a live REMOTE-dispatched minion unable to authenticate: an operator added
`CLAUDE_CODE_OAUTH_TOKEN` to an existing session's `extra_env` via the Configuration
Modal, and REMOTE never received it.

Same two-in-process-ClaudeWebUI-instances-over-ASGITransport pattern as
test_batch2_relay.py — a real headless ClaudeWebUI stands in for REMOTE.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI

REMOTE_TOKEN = "remote-test-token-499"


def _make_remote(tmp_path):
    return ClaudeWebUI(
        data_dir=tmp_path / "remote_data",
        backend_mode="headless",
        backend_auth_token=REMOTE_TOKEN,
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


async def _create_remote_session(client: AsyncClient, tmp_path) -> tuple[str, str]:
    project_resp = await client.post(
        "/api/projects", json={"name": "cfg-relay-project", "working_directory": str(tmp_path)}
    )
    assert project_resp.status_code == 200, project_resp.text
    project_id = project_resp.json()["project"]["project_id"]

    session_resp = await client.post(
        "/api/sessions", json={"project_id": project_id, "session_id": "cfg-relay-session"}
    )
    assert session_resp.status_code == 200, session_resp.text
    return project_id, session_resp.json()["session_id"]


@pytest.mark.asyncio
async def test_patch_session_config_relays_extra_env_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        _, session_id = await _create_remote_session(client, tmp_path)

        # Config change made AFTER creation — this is exactly the case that used to
        # silently vanish: only session-creation config reached REMOTE. extra_env has
        # no dedicated flat field on SessionUpdateRequest, so the Configuration Modal
        # sends it via the wholesale "config" dict replacement (issue #1230) — the
        # actual path that carried a real operator's CLAUDE_CODE_OAUTH_TOKEN nowhere.
        patch_resp = await client.patch(
            f"/api/sessions/{session_id}",
            json={
                "config": {
                    "model": "opus",
                    "extra_env": {"CLAUDE_CODE_OAUTH_TOKEN": "secret-token"},
                }
            },
        )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["success"] is True

    remote_info = await remote.coordinator.session_manager.get_session_info(session_id)
    assert remote_info is not None
    assert remote_info.config.get("model") == "opus"
    assert remote_info.config.get("extra_env") == {"CLAUDE_CODE_OAUTH_TOKEN": "secret-token"}

    # Hub's own local copy stays in sync too (existing Batch-1 bookkeeping behavior).
    hub_info = await hub.coordinator.session_manager.get_session_info(session_id)
    assert hub_info.config.get("model") == "opus"


@pytest.mark.asyncio
async def test_patch_session_config_not_relayed_in_local_mode(tmp_path):
    """Sanity check: LOCAL mode's existing behavior is unchanged — no REMOTE involved."""
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_local")
    (hub.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        project_resp = await client.post(
            "/api/projects", json={"name": "local-project", "working_directory": str(tmp_path)}
        )
        project_id = project_resp.json()["project"]["project_id"]
        session_resp = await client.post(
            "/api/sessions", json={"project_id": project_id, "session_id": "local-session"}
        )
        session_id = session_resp.json()["session_id"]

        patch_resp = await client.patch(
            f"/api/sessions/{session_id}", json={"model": "opus"}
        )
    assert patch_resp.status_code == 200, patch_resp.text

    info = await hub.coordinator.session_manager.get_session_info(session_id)
    assert info.config.get("model") == "opus"


@pytest.mark.asyncio
async def test_session_name_update_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_name")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        _, session_id = await _create_remote_session(client, tmp_path)

        name_resp = await client.put(
            f"/api/sessions/{session_id}/name", json={"name": "renamed-on-remote"}
        )
    assert name_resp.status_code == 200, name_resp.text

    remote_info = await remote.coordinator.session_manager.get_session_info(session_id)
    assert remote_info.name == "renamed-on-remote"


@pytest.mark.asyncio
async def test_set_permission_mode_on_stopped_remote_session_relays(tmp_path):
    """A session that hasn't been started yet is in a STOPPED_STATE — the
    persist-only branch of set_permission_mode used to skip the REMOTE relay
    entirely."""
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_permmode")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        _, session_id = await _create_remote_session(client, tmp_path)

        resp = await client.post(
            f"/api/sessions/{session_id}/permission-mode", json={"mode": "plan"}
        )
    assert resp.status_code == 200, resp.text

    remote_info = await remote.coordinator.session_manager.get_session_info(session_id)
    assert remote_info.config.get("permission_mode") == "plan"


@pytest.mark.asyncio
async def test_set_model_on_stopped_remote_session_relays(tmp_path):
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_model")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        _, session_id = await _create_remote_session(client, tmp_path)

        resp = await client.post(
            f"/api/sessions/{session_id}/model", json={"model": "opus"}
        )
    assert resp.status_code == 200, resp.text

    remote_info = await remote.coordinator.session_manager.get_session_info(session_id)
    assert remote_info.config.get("model") == "opus"
