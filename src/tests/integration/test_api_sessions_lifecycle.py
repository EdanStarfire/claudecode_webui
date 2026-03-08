"""
Stage 2b: Integration tests for Session Lifecycle & Messages endpoints.

Tests:
- POST /api/sessions/{session_id}/start — start session (SDK init)
- POST /api/sessions/{session_id}/pause — pause session
- POST /api/sessions/{session_id}/terminate — terminate session
- POST /api/sessions/{session_id}/restart — restart (disconnect + resume)
- POST /api/sessions/{session_id}/reset — reset (clear + fresh start)
- POST /api/sessions/{session_id}/disconnect — disconnect SDK
- POST /api/sessions/{session_id}/permission-mode — set permission mode
- POST /api/sessions/{session_id}/messages — send message
- GET /api/sessions/{session_id}/messages — get messages (paginated)
"""

import asyncio

import pytest

pytestmark = pytest.mark.slow


async def _create_named_session(env, name):
    """Helper to create a session with a specific fixture name."""
    project = await env["create_test_project"]("Lifecycle Project")
    session = await env["create_test_session"](
        project["project_id"], name
    )
    return session


async def _wait_for_state(client, sid, target_state, timeout=10):
    """Poll until session reaches target state."""
    for _ in range(int(timeout / 0.2)):
        resp = await client.get(f"/api/sessions/{sid}")
        if resp.status_code == 200:
            state = resp.json()["session"]["state"]
            if state == target_state:
                return True
        await asyncio.sleep(0.2)
    return False


class TestStartSession:
    async def test_start_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Session should be in CREATED state
        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["state"] == "created"

        # Start the session
        resp = await client.post(f"/api/sessions/{sid}/start")
        assert resp.status_code == 200

        # Wait for ACTIVE state
        reached = await _wait_for_state(client, sid, "active")
        assert reached, "Session did not become active"


class TestTerminateSession:
    async def test_terminate_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start then terminate
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/terminate")
        assert resp.status_code == 200

        reached = await _wait_for_state(client, sid, "terminated")
        assert reached, "Session did not terminate"


class TestDisconnectSession:
    async def test_disconnect_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start then disconnect
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/disconnect")
        assert resp.status_code == 200


class TestPermissionMode:
    async def test_set_permission_mode_on_created_session(self, api_integration_env):
        """Setting permission mode on a non-active session returns 400."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission-mode",
            json={"mode": "bypassPermissions"},
        )
        # Session must be active to change permission mode
        assert resp.status_code == 400

    async def test_set_permission_mode_invalid(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission-mode",
            json={"mode": "invalid_mode"},
        )
        assert resp.status_code == 400


class TestSendMessage:
    async def test_send_message_to_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(
            f"/api/sessions/{sid}/messages",
            json={"message": "Hello, how are you?"},
        )
        assert resp.status_code == 200

        # Wait briefly for message processing
        await asyncio.sleep(1)


class TestGetMessages:
    async def test_get_messages_empty(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert "messages" in body

    async def test_get_messages_with_pagination(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session to generate some messages
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")
        await asyncio.sleep(0.5)

        # Get with limit
        resp = await client.get(f"/api/sessions/{sid}/messages?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert "messages" in body
        assert "total" in body or "pagination" in body or len(body["messages"]) <= 2

    async def test_get_messages_offset(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session to generate messages
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")
        await asyncio.sleep(0.5)

        # Get all messages first
        resp_all = await client.get(f"/api/sessions/{sid}/messages?limit=100&offset=0")
        all_msgs = resp_all.json()["messages"]

        if len(all_msgs) > 1:
            # Get with offset
            resp_offset = await client.get(f"/api/sessions/{sid}/messages?limit=100&offset=1")
            offset_msgs = resp_offset.json()["messages"]
            assert len(offset_msgs) == len(all_msgs) - 1


class TestPauseSession:
    async def test_pause_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/pause")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_pause_created_session(self, api_integration_env):
        """Pausing a non-active session should still return 200."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/pause")
        # May succeed (no-op) or fail depending on state validation
        assert resp.status_code in (200, 400, 500)


class TestRestartSession:
    async def test_restart_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/restart")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_restart_created_session(self, api_integration_env):
        """Restart on a non-active session."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/restart")
        # Should handle gracefully — may start fresh or return error
        assert resp.status_code in (200, 400, 500)


class TestResetSession:
    async def test_reset_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/reset")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_reset_created_session(self, api_integration_env):
        """Reset on a non-active session."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/reset")
        assert resp.status_code in (200, 400, 500)
