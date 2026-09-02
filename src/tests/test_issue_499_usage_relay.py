"""Regression test for issue #499's /usage relay fix.

GET /api/sessions/{id}/usage previously read only the Hub's own local
analytics_store unconditionally — but usage rows only ever get written inside
SessionCoordinator._create_message_callback's 'result' handling, which never
runs on the Hub for a REMOTE-dispatched session (the relay bypasses it
entirely). So the endpoint 404'd "No usage data for this session" permanently
for every REMOTE session, regardless of how many turns REMOTE actually ran.

Same two-in-process-ClaudeWebUI-instances-over-ASGITransport pattern as
test_batch2_relay.py — a real headless ClaudeWebUI stands in for REMOTE.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI

REMOTE_TOKEN = "remote-test-token-499-usage"


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
async def test_usage_relays_to_remote_and_recomputes_cost_with_hub_pricing(tmp_path):
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        project_resp = await client.post(
            "/api/projects", json={"name": "usage-relay-project", "working_directory": str(tmp_path)}
        )
        assert project_resp.status_code == 200, project_resp.text
        project_id = project_resp.json()["project"]["project_id"]

        session_resp = await client.post(
            "/api/sessions", json={"project_id": project_id, "session_id": "usage-relay-session"}
        )
        assert session_resp.status_code == 200, session_resp.text
        session_id = session_resp.json()["session_id"]

        # Usage data only ever gets recorded on REMOTE (this is what
        # _create_message_callback's 'result' handling would have done, had it run
        # on REMOTE for a real turn) — the Hub's own analytics_store never gets a row.
        # analytics_store is normally wired up by initialize()'s audit subsystem
        # section; wire it directly here rather than paying for a full initialize().
        await remote.analytics_db.initialize()
        remote.coordinator.set_analytics_store(remote.analytics_store)
        await remote.coordinator.analytics_store.record_turn(
            session_id,
            turn_seq=1,
            model="claude-sonnet-5",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            sdk_total_cost_usd=0.0,
        )

        response = await client.get(f"/api/sessions/{session_id}/usage")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["turn_count"] == 1
    assert body["input_tokens"] == 100
    assert body["output_tokens"] == 50
    assert body["model"] == "claude-sonnet-5"
    # The Hub's own analytics_store is never even wired up in this test (only
    # REMOTE's is) — a 200 with correct data here is only possible if the
    # response actually came from REMOTE via the relay, not a Hub-local read
    # (which would 500 on a None analytics_store instead).
    assert hub.coordinator.analytics_store is None


@pytest.mark.asyncio
async def test_usage_404s_when_remote_has_no_data(tmp_path):
    remote = _make_remote(tmp_path)
    (remote.coordinator.data_dir / "projects").mkdir(parents=True, exist_ok=True)
    (remote.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data_404")
    (hub.coordinator.data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    _wire_hub_to_remote(hub, remote)

    async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
        project_resp = await client.post(
            "/api/projects", json={"name": "usage-404-project", "working_directory": str(tmp_path)}
        )
        project_id = project_resp.json()["project"]["project_id"]
        session_resp = await client.post(
            "/api/sessions", json={"project_id": project_id, "session_id": "usage-404-session"}
        )
        session_id = session_resp.json()["session_id"]

        response = await client.get(f"/api/sessions/{session_id}/usage")

    assert response.status_code == 404
