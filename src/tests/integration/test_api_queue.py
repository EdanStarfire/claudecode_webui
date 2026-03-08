"""
Stage 4: Integration tests for Queue endpoints (7 routes).

Tests:
- POST /api/sessions/{session_id}/queue-message — enqueue message
- GET /api/sessions/{session_id}/queue — list queue items
- DELETE /api/sessions/{session_id}/queue/{queue_id} — cancel item
- POST /api/sessions/{session_id}/queue/{queue_id}/requeue — re-enqueue item
- DELETE /api/sessions/{session_id}/queue — clear queue
- PUT /api/sessions/{session_id}/queue/pause — pause/resume processing
- PUT /api/sessions/{session_id}/queue/config — update queue config
"""

import pytest

pytestmark = pytest.mark.slow


async def _create_session(env):
    project = await env["create_test_project"]("Queue Test")
    session = await env["create_test_session"](project["project_id"], "QueueSession")
    return session


class TestEnqueueMessage:
    async def test_enqueue_message(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/queue-message",
            json={"content": "Hello from the queue"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "queue_id" in body
        assert "position" in body
        assert "item" in body

    async def test_enqueue_multiple(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        for i in range(3):
            resp = await client.post(
                f"/api/sessions/{sid}/queue-message",
                json={"content": f"Message {i}"},
            )
            assert resp.status_code == 200

        # Check queue
        resp = await client.get(f"/api/sessions/{sid}/queue")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3

    async def test_enqueue_empty_content(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/queue-message",
            json={"content": ""},
        )
        assert resp.status_code == 400

    async def test_enqueue_with_reset(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/queue-message",
            json={"content": "Reset and run", "reset_session": True},
        )
        assert resp.status_code == 200
        item = resp.json()["item"]
        assert item.get("reset_session") is True


class TestGetQueue:
    async def test_get_empty_queue(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/queue")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["pending_count"] == 0


class TestCancelQueueItem:
    async def test_cancel_pending_item(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        # Enqueue
        enqueue_resp = await client.post(
            f"/api/sessions/{sid}/queue-message",
            json={"content": "To cancel"},
        )
        queue_id = enqueue_resp.json()["queue_id"]

        # Cancel
        resp = await client.delete(f"/api/sessions/{sid}/queue/{queue_id}")
        assert resp.status_code == 200

        # Verify cancelled
        queue_resp = await client.get(f"/api/sessions/{sid}/queue")
        pending = [i for i in queue_resp.json()["items"] if i["status"] == "pending"]
        assert len(pending) == 0

    async def test_cancel_nonexistent_item(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}/queue/nonexistent")
        assert resp.status_code == 404


class TestClearQueue:
    async def test_clear_queue(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        # Enqueue some items
        for i in range(3):
            await client.post(
                f"/api/sessions/{sid}/queue-message",
                json={"content": f"Msg {i}"},
            )

        # Clear
        resp = await client.delete(f"/api/sessions/{sid}/queue")
        assert resp.status_code == 200
        assert resp.json()["cancelled_count"] == 3

        # Verify empty
        queue_resp = await client.get(f"/api/sessions/{sid}/queue")
        pending = [i for i in queue_resp.json()["items"] if i["status"] == "pending"]
        assert len(pending) == 0

    async def test_clear_empty_queue(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}/queue")
        assert resp.status_code == 200
        assert resp.json()["cancelled_count"] == 0


class TestPauseQueue:
    async def test_pause_queue(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.put(
            f"/api/sessions/{sid}/queue/pause",
            json={"paused": True},
        )
        assert resp.status_code == 200
        assert resp.json()["paused"] is True

    async def test_resume_queue(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        # Pause first
        await client.put(f"/api/sessions/{sid}/queue/pause", json={"paused": True})

        # Resume
        resp = await client.put(f"/api/sessions/{sid}/queue/pause", json={"paused": False})
        assert resp.status_code == 200
        assert resp.json()["paused"] is False


class TestQueueConfig:
    async def test_update_config(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.put(
            f"/api/sessions/{sid}/queue/config",
            json={"min_wait_seconds": 20, "min_idle_seconds": 15},
        )
        assert resp.status_code == 200
        assert resp.json()["config"]["min_wait_seconds"] == 20
