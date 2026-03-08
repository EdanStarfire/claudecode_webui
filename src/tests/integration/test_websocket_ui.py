"""
Integration tests for UI WebSocket endpoint (/ws/ui).

Tests global broadcasts: sessions_list on connect, state_change on session
start, project_updated on project mutation, multi-client broadcasts, and
ping/pong keepalive.
"""

import time

import pytest


@pytest.mark.timeout(15)
def test_ui_ws_receives_sessions_list(ws_integration_env):
    """Connect to /ws/ui, verify first message is sessions_list."""
    client = ws_integration_env["test_client"]

    with client.websocket_connect("/ws/ui") as ws:
        msg = ws.receive_json(mode="text")
        assert msg["type"] == "sessions_list"
        assert "data" in msg
        assert "sessions" in msg["data"]


@pytest.mark.timeout(15)
def test_ui_ws_state_change_broadcast(ws_integration_env):
    """Start a session and verify the UI WS receives state_change."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]

    with client.websocket_connect("/ws/ui") as ws:
        # Consume initial sessions_list
        ws.receive_json(mode="text")

        # Start the session
        env["start_session"](session_id)

        # Collect messages until we see a state_change for this session
        found = False
        for _ in range(30):
            try:
                msg = ws.receive_json(mode="text")
            except Exception:
                time.sleep(0.2)
                continue
            if msg.get("type") == "state_change":
                data = msg.get("data", {})
                if data.get("session_id") == session_id:
                    found = True
                    break
        assert found, "Did not receive state_change broadcast for session"


@pytest.mark.timeout(15)
def test_ui_ws_multi_client_broadcast(ws_integration_env):
    """Two UI WS clients both receive broadcasts."""
    env = ws_integration_env
    client = env["test_client"]

    with (
        client.websocket_connect("/ws/ui") as ws1,
        client.websocket_connect("/ws/ui") as ws2,
    ):
        # Consume initial sessions_list from both
        ws1.receive_json(mode="text")
        ws2.receive_json(mode="text")

        # Create a project to trigger a broadcast
        project = env["create_project"]("Broadcast Test")

        # Both clients should receive project_updated or state_change
        def receive_until_type(ws, target_type, max_attempts=20):
            for _ in range(max_attempts):
                try:
                    msg = ws.receive_json(mode="text")
                    if msg.get("type") == target_type:
                        return msg
                except Exception:
                    time.sleep(0.1)
            return None

        # Creating a project doesn't broadcast, but creating a session does
        env["create_session"](project["project_id"])

        msg1 = receive_until_type(ws1, "state_change")
        msg2 = receive_until_type(ws2, "state_change")
        assert msg1 is not None, "Client 1 did not receive broadcast"
        assert msg2 is not None, "Client 2 did not receive broadcast"


@pytest.mark.timeout(15)
def test_ui_ws_project_update_broadcast(ws_integration_env):
    """Updating a project broadcasts project_updated to UI WS."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]("Update Test")
    project_id = project["project_id"]

    with client.websocket_connect("/ws/ui") as ws:
        # Consume initial sessions_list
        ws.receive_json(mode="text")

        # Toggle expansion (PUT endpoint) to trigger project_updated broadcast
        resp = client.put(f"/api/projects/{project_id}/toggle-expansion")
        assert resp.status_code == 200

        # Collect messages — server sends pings on 3s timeout, so we will
        # receive either project_updated or ping messages.
        found = False
        for _ in range(20):
            msg = ws.receive_json(mode="text")
            if msg.get("type") == "project_updated":
                found = True
                break
        assert found, "Did not receive project_updated broadcast"


@pytest.mark.timeout(15)
def test_ui_ws_ping_pong(ws_integration_env):
    """Send ping to UI WS, verify pong response."""
    client = ws_integration_env["test_client"]

    with client.websocket_connect("/ws/ui") as ws:
        # Consume initial sessions_list
        ws.receive_json(mode="text")

        # Send ping
        ws.send_json({"type": "ping"})

        # Server may send its own pings (3s timeout), so collect until pong
        found = False
        for _ in range(20):
            try:
                msg = ws.receive_json(mode="text")
            except Exception:
                time.sleep(0.1)
                continue
            if msg.get("type") == "pong":
                found = True
                break
        assert found, "Did not receive pong response"
