"""
Integration test for issue #1746 (stage: backend) — GET /api/sessions/{id}/background_agents.

Uses the shared api_integration_env fixture (full FastAPI app + real
SessionCoordinator) to verify the endpoint hydrates a TaskLegRegistry from
stored messages and returns the expected response shape.
"""

from __future__ import annotations

import pytest


def _stored_task_started(task_id, tool_use_id, session_id, ts):
    return {
        "_type": "TaskStartedMessage",
        "timestamp": ts,
        "session_id": session_id,
        "data": {
            "subtype": "task_started",
            "data": {},
            "task_id": task_id,
            "description": "alpha: hydrated",
            "uuid": f"uuid-{task_id}-started",
            "session_id": "sub-" + session_id,
            "tool_use_id": tool_use_id,
            "task_type": "local_agent",
        },
    }


def _stored_task_notification(task_id, tool_use_id, session_id, status, ts):
    return {
        "_type": "TaskNotificationMessage",
        "timestamp": ts,
        "session_id": session_id,
        "data": {
            "subtype": "task_notification",
            "data": {},
            "task_id": task_id,
            "status": status,
            "output_file": "",
            "summary": "done",
            "uuid": f"uuid-{task_id}-notif",
            "session_id": "sub-" + session_id,
            "tool_use_id": tool_use_id,
            "usage": None,
        },
    }


@pytest.mark.asyncio
async def test_background_agents_endpoint_returns_hydrated_snapshot(api_integration_env):
    """GET /api/sessions/{id}/background_agents hydrates from stored messages
    and returns the expected response shape."""
    env = api_integration_env
    client = env["client"]
    coordinator = env["coordinator"]

    project = await env["create_test_project"]()
    session = await env["create_test_session"](project["project_id"])
    session_id = session["session_id"]

    session_dir = await coordinator.session_manager.get_session_directory(session_id)
    storage = coordinator._storage_managers.get(session_id)
    if storage is None:
        from src.data_storage import DataStorageManager
        storage = DataStorageManager(session_dir)
        await storage.initialize()
        coordinator._storage_managers[session_id] = storage

    for msg in (
        _stored_task_started("t1", "tu-1", session_id, 1.0),
        _stored_task_notification("t1", "tu-1", session_id, "completed", 2.0),
    ):
        await storage.append_message(msg)

    resp = await client.get(f"/api/sessions/{session_id}/background_agents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert len(body["agents"]) == 1
    agent = body["agents"][0]
    assert agent["task_id"] == "t1"
    assert agent["current_status"] == "completed"
    assert len(agent["legs"]) == 1
    assert agent["legs"][0]["tool_use_id"] == "tu-1"


@pytest.mark.asyncio
async def test_background_agents_endpoint_404_for_unknown_session(api_integration_env):
    client = api_integration_env["client"]
    resp = await client.get("/api/sessions/does-not-exist/background_agents")
    assert resp.status_code == 404
