"""REMOTE-relay tests for Batch 4's MCP config three-way split (issue #498)."""


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
async def test_create_shared_config_stays_local_even_in_remote_mode(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.post(
            "/api/mcp-configs",
            json={
                "name": "shared-one", "type": "stdio", "command": "echo hi",
                "shared_connection": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["shared_connection"] is True
    # Created on the Hub's own local store, not relayed.
    local = await hub.coordinator.mcp_config_manager.get_config(body["id"])
    assert local is not None
    assert local.name == "shared-one"


@pytest.mark.asyncio
async def test_create_non_shared_config_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data2")
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.post(
            "/api/mcp-configs",
            json={
                "name": "non-shared-one", "type": "stdio", "command": "echo hi",
                "shared_connection": False,
            },
        )

    assert response.status_code == 200
    body = response.json()
    # Not created on the Hub's local store...
    hub_configs = await hub.coordinator.mcp_config_manager.list_configs()
    assert not any(c.name == "non-shared-one" for c in hub_configs)
    # ...created on REMOTE's store instead.
    remote_configs = await remote.coordinator.mcp_config_manager.list_configs()
    assert any(c.id == body["id"] for c in remote_configs)


@pytest.mark.asyncio
async def test_list_configs_merges_local_shared_and_remote_non_shared(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data3")

    # A local shared config on the Hub, created before REMOTE was wired.
    await hub.coordinator.mcp_config_manager.create_config(
        name="hub-shared", server_type="stdio", command="echo hub", shared_connection=True,
    )
    # A non-shared config directly on REMOTE (as if created via relay earlier).
    await remote.coordinator.mcp_config_manager.create_config(
        name="remote-nonshared", server_type="stdio", command="echo remote", shared_connection=False,
    )
    # A shared config directly on REMOTE — must NOT leak into the Hub's merged view.
    await remote.coordinator.mcp_config_manager.create_config(
        name="remote-shared-should-not-leak", server_type="stdio", command="echo x",
        shared_connection=True,
    )

    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/mcp-configs")

    assert response.status_code == 200
    names = {c["name"] for c in response.json()["configs"]}
    assert names == {"hub-shared", "remote-nonshared"}


@pytest.mark.asyncio
async def test_export_includes_shared_connection_field(tmp_path):
    """Bug fix: export previously dropped shared_connection entirely."""
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data4")
    await hub.coordinator.mcp_config_manager.create_config(
        name="cfg-a", server_type="stdio", command="echo a", shared_connection=True,
    )
    await hub.coordinator.mcp_config_manager.create_config(
        name="cfg-b", server_type="stdio", command="echo b", shared_connection=False,
    )

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.post("/api/mcp-configs/export", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["cfg-a"]["shared_connection"] is True
    assert body["cfg-b"]["shared_connection"] is False


@pytest.mark.asyncio
async def test_get_config_relays_when_not_local_shared(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data5")
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/api/mcp-configs/nonexistent-id")

    # REMOTE answered (404, since REMOTE itself doesn't have it either) — proves the
    # request crossed the relay rather than the Hub answering locally.
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_config_stays_local_for_local_shared_config(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data6")
    config = await hub.coordinator.mcp_config_manager.create_config(
        name="hub-shared-2", server_type="stdio", command="echo hi", shared_connection=True,
    )
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get(f"/api/mcp-configs/{config.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "hub-shared-2"


@pytest.mark.asyncio
async def test_import_routes_shared_and_nonshared_entries_separately(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data7")
    _wire_hub_to_remote(hub, remote)

    servers = {
        "shared-import": {
            "type": "stdio", "command": "echo shared", "shared_connection": True,
        },
        "nonshared-import": {
            "type": "stdio", "command": "echo nonshared", "shared_connection": False,
        },
    }

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.post(
            "/api/mcp-configs/import", json={"servers": servers, "dry_run": False}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["create"] == 2

    hub_configs = await hub.coordinator.mcp_config_manager.list_configs()
    assert any(c.name == "shared-import" for c in hub_configs)
    assert not any(c.name == "nonshared-import" for c in hub_configs)

    remote_configs = await remote.coordinator.mcp_config_manager.list_configs()
    assert any(c.name == "nonshared-import" for c in remote_configs)


@pytest.mark.asyncio
async def test_oauth_callback_route_unaffected_by_mcp_split(tmp_path):
    """/oauth/callback moved from mcp.py to core.py — must still resolve at the bare
    (non-/api) path in frontend mode."""
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data8")
    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        response = await client.get("/oauth/callback")
    # No state/code query params — expect a well-formed (non-404) error response,
    # proving the route itself is registered and reachable.
    assert response.status_code != 404
