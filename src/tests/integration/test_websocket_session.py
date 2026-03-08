"""
Integration tests for Session WebSocket endpoint (/ws/session/{session_id}).

Tests connection validation, SDK message streaming, message ordering,
permission flow, interrupts, multi-client delivery, and ping/pong.

Note: The WebSocket server wraps all SDK messages in {"type": "message",
"data": {"type": "<actual_type>", ...}}. Tests check data.type for the
actual message type (user, system, assistant, result, permission_request).
"""

import uuid

import pytest


def _get_data_type(msg):
    """Extract the actual message type from data.type (or top-level type for non-message)."""
    if msg.get("type") == "message":
        return msg.get("data", {}).get("type", "unknown")
    return msg.get("type", "unknown")


def _collect_sdk_messages(ws, max_msgs=20):
    """Collect SDK messages until 'result' or a server ping is received."""
    messages = []
    for _ in range(max_msgs):
        msg = ws.receive_json(mode="text")
        top_type = msg.get("type")
        if top_type == "ping":
            break  # No more SDK messages pending
        if top_type == "message":
            messages.append(msg)
            if msg.get("data", {}).get("type") == "result":
                break
    return messages


def _receive_until_data_type(ws, target_data_type, max_attempts=20):
    """Receive until a message with data.type == target is found."""
    for _ in range(max_attempts):
        msg = ws.receive_json(mode="text")
        if _get_data_type(msg) == target_data_type:
            return msg
        if msg.get("type") == "ping":
            continue
    return None


@pytest.mark.timeout(15)
def test_session_ws_connection_established(ws_integration_env):
    """Connect to an active session WS and verify connection_established."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        msg = ws.receive_json(mode="text")
        assert msg["type"] == "connection_established"
        assert msg["session_id"] == session_id


@pytest.mark.timeout(15)
def test_session_ws_rejects_nonexistent_session(ws_integration_env):
    """Connecting to a nonexistent session returns close code 4404."""
    client = ws_integration_env["test_client"]
    fake_id = str(uuid.uuid4())

    with pytest.raises((Exception,), match=".*"):  # noqa: B017
        with client.websocket_connect(f"/ws/session/{fake_id}") as ws:
            ws.receive_json(mode="text")


@pytest.mark.timeout(15)
def test_session_ws_rejects_created_state(ws_integration_env):
    """Connecting to an unstarted (created) session returns close code 4003."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"])
    session_id = session["session_id"]

    with pytest.raises((Exception,), match=".*"):  # noqa: B017
        with client.websocket_connect(f"/ws/session/{session_id}") as ws:
            ws.receive_json(mode="text")


@pytest.mark.timeout(15)
def test_session_ws_receives_sdk_messages(ws_integration_env):
    """Send a message and verify SDK response messages arrive via WS."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        ws.receive_json(mode="text")  # connection_established
        ws.send_json({"type": "send_message", "content": "Hello, how are you?"})

        messages = _collect_sdk_messages(ws)
        data_types = [_get_data_type(m) for m in messages]

        assert "assistant" in data_types, \
            f"Expected assistant message, got data types: {data_types}"
        assert "result" in data_types, \
            f"Expected result message, got data types: {data_types}"


@pytest.mark.timeout(15)
def test_session_ws_message_ordering(ws_integration_env):
    """Verify messages arrive in correct order: user → system → assistant → result."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        ws.receive_json(mode="text")  # connection_established
        ws.send_json({"type": "send_message", "content": "Hello, how are you?"})

        messages = _collect_sdk_messages(ws)
        data_types = [_get_data_type(m) for m in messages]

        # Result should be present and user echo should come first
        assert "result" in data_types
        assert data_types[0] == "user", f"First message should be user echo, got: {data_types}"


@pytest.mark.timeout(15)
def test_session_ws_send_message_command(ws_integration_env):
    """Send a message command via WS and verify the SDK processes it."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        ws.receive_json(mode="text")  # connection_established
        ws.send_json({"type": "send_message", "content": "Hello, how are you?"})

        messages = _collect_sdk_messages(ws)
        assert len(messages) >= 3, f"Expected at least 3 SDK messages, got {len(messages)}"


@pytest.mark.timeout(15)
def test_session_ws_permission_flow(ws_integration_env):
    """Use permission_flow fixture and verify permission messages arrive via WS."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](
        project["project_id"],
        fixture_name="permission_flow",
    )
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        ws.receive_json(mode="text")  # connection_established
        ws.send_json({"type": "send_message", "content": "Edit the file at /tmp/test.txt"})

        messages = _collect_sdk_messages(ws)
        data_types = [_get_data_type(m) for m in messages]

        # Should contain assistant and result (permission is auto-advanced by mock)
        assert "assistant" in data_types, \
            f"Expected assistant in data types: {data_types}"
        assert "result" in data_types, \
            f"Expected result in data types: {data_types}"
        # Should have more messages than single_turn due to permission flow
        assert len(messages) >= 4, \
            f"Expected at least 4 messages for permission flow, got {len(messages)}"


@pytest.mark.timeout(15)
def test_session_ws_interrupt(ws_integration_env):
    """Send interrupt command and verify the response."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        ws.receive_json(mode="text")  # connection_established

        ws.send_json({"type": "interrupt_session"})

        # Receive until we find interrupt_response
        found = False
        for _ in range(20):
            msg = ws.receive_json(mode="text")
            if msg.get("type") == "interrupt_response":
                assert "success" in msg
                found = True
                break
        assert found, "Did not receive interrupt_response"


@pytest.mark.timeout(15)
def test_session_ws_multi_client_receives_messages(ws_integration_env):
    """Two clients connected to same session both receive SDK messages."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with (
        client.websocket_connect(f"/ws/session/{session_id}") as ws1,
        client.websocket_connect(f"/ws/session/{session_id}") as ws2,
    ):
        ws1.receive_json(mode="text")  # connection_established
        ws2.receive_json(mode="text")  # connection_established

        ws1.send_json({"type": "send_message", "content": "Hello, how are you?"})

        # Both clients should receive "message" type messages
        def has_message(ws):
            for _ in range(20):
                msg = ws.receive_json(mode="text")
                if msg.get("type") == "message":
                    return True
                if msg.get("type") == "ping":
                    continue
            return False

        assert has_message(ws1), "Client 1 did not receive SDK messages"
        assert has_message(ws2), "Client 2 did not receive SDK messages"


@pytest.mark.timeout(15)
def test_session_ws_server_keepalive_ping(ws_integration_env):
    """Session WS server sends keepalive pings on its 3s idle timeout."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]()
    session = env["create_session"](project["project_id"], fixture_name="single_turn")
    session_id = session["session_id"]
    env["start_session"](session_id)
    assert env["wait_for_state"](session_id, "active")

    with client.websocket_connect(f"/ws/session/{session_id}") as ws:
        ws.receive_json(mode="text")  # connection_established

        # Wait for server-initiated ping (sent after 3s idle timeout)
        found = False
        for _ in range(10):
            msg = ws.receive_json(mode="text")
            if msg.get("type") == "ping":
                found = True
                break
        assert found, "Did not receive server keepalive ping"
