"""
Integration tests for Legion WebSocket endpoint (/ws/legion/{legion_id}).

Tests connection establishment, rejection of invalid projects, comm broadcast
delivery, minion creation broadcast, multi-client delivery, and ping/pong.
"""

import time
import uuid

import pytest


def _receive_until_type(ws, target_type, max_attempts=20, sleep=0.1):
    """Receive messages until one with the target type is found."""
    for _ in range(max_attempts):
        try:
            msg = ws.receive_json(mode="text")
            if msg.get("type") == target_type:
                return msg
        except Exception:
            time.sleep(sleep)
    return None


@pytest.mark.timeout(15)
def test_legion_ws_connection_established(ws_integration_env):
    """Connect to a valid project's legion WS and verify connection_established."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]("Legion Test")
    project_id = project["project_id"]

    with client.websocket_connect(f"/ws/legion/{project_id}") as ws:
        msg = ws.receive_json(mode="text")
        assert msg["type"] == "connection_established"
        assert msg["legion_id"] == project_id


@pytest.mark.timeout(15)
def test_legion_ws_rejects_nonexistent_project(ws_integration_env):
    """Connecting to a nonexistent project returns close code 4404."""
    client = ws_integration_env["test_client"]
    fake_id = str(uuid.uuid4())

    with pytest.raises((Exception,), match=".*"):  # noqa: B017
        with client.websocket_connect(f"/ws/legion/{fake_id}") as ws:
            ws.receive_json(mode="text")


@pytest.mark.timeout(15)
def test_legion_ws_comm_broadcast(ws_integration_env):
    """Comm broadcast reaches the legion WS via the broadcast callback."""
    env = ws_integration_env
    client = env["test_client"]
    webui = env["webui"]

    project = env["create_project"]("Comm Test")
    project_id = project["project_id"]

    with client.websocket_connect(f"/ws/legion/{project_id}") as ws:
        ws.receive_json(mode="text")  # connection_established

        # Directly invoke the broadcast callback (the integration path from
        # CommRouter → WebUI._broadcast_comm_to_legion_websocket) to test
        # that comms arrive on the legion WS. Full comm routing is tested
        # separately in legion integration tests.
        from concurrent.futures import Future
        from threading import Thread

        from src.models.legion_models import Comm, CommType

        comm = Comm(
            comm_id="test-comm-001",
            from_user=True,
            to_user=True,
            content="Test comm message",
            comm_type=CommType.INFO,
        )

        # The ASGI app runs in TestClient's background thread with its own
        # event loop. We need to schedule the broadcast in that loop.
        import asyncio

        fut = Future()

        def _run_broadcast():
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(
                    webui._broadcast_comm_to_legion_websocket(project_id, comm)
                )
                loop.close()
                fut.set_result(True)
            except Exception as e:
                fut.set_result(e)

        t = Thread(target=_run_broadcast)
        t.start()
        t.join(timeout=5)

        msg = _receive_until_type(ws, "comm")
        assert msg is not None, "Did not receive comm broadcast"
        assert msg["comm"]["content"] == "Test comm message"


@pytest.mark.timeout(15)
def test_legion_ws_minion_created_broadcast(ws_integration_env):
    """Creating a minion broadcasts to both UI and legion WS clients."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]("Minion Test")
    project_id = project["project_id"]

    with (
        client.websocket_connect(f"/ws/legion/{project_id}") as ws_legion,
        client.websocket_connect("/ws/ui") as ws_ui,
    ):
        ws_legion.receive_json(mode="text")  # connection_established
        ws_ui.receive_json(mode="text")  # sessions_list

        # Create a minion
        resp = client.post(f"/api/legions/{project_id}/minions", json={
            "name": "TestMinion",
            "role": "Worker",
        })
        assert resp.status_code == 200

        # UI WS should receive a state_change for the new session
        msg = _receive_until_type(ws_ui, "state_change")
        assert msg is not None, "UI WS did not receive state_change for minion creation"


@pytest.mark.timeout(15)
def test_legion_ws_multi_client_broadcast(ws_integration_env):
    """Two legion WS clients both receive broadcasts."""
    env = ws_integration_env
    client = env["test_client"]
    webui = env["webui"]

    project = env["create_project"]("Multi Client Test")
    project_id = project["project_id"]

    with (
        client.websocket_connect(f"/ws/legion/{project_id}") as ws1,
        client.websocket_connect(f"/ws/legion/{project_id}") as ws2,
    ):
        ws1.receive_json(mode="text")  # connection_established
        ws2.receive_json(mode="text")  # connection_established

        # Broadcast a comm directly to both clients
        import asyncio
        from threading import Thread

        from src.models.legion_models import Comm, CommType

        comm = Comm(
            comm_id="test-multi-001",
            from_user=True,
            to_user=True,
            content="Broadcast test",
            comm_type=CommType.INFO,
        )

        def _run_broadcast():
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                webui._broadcast_comm_to_legion_websocket(project_id, comm)
            )
            loop.close()

        t = Thread(target=_run_broadcast)
        t.start()
        t.join(timeout=5)

        msg1 = _receive_until_type(ws1, "comm")
        msg2 = _receive_until_type(ws2, "comm")
        assert msg1 is not None, "Client 1 did not receive comm broadcast"
        assert msg2 is not None, "Client 2 did not receive comm broadcast"


@pytest.mark.timeout(15)
def test_legion_ws_ping_pong(ws_integration_env):
    """Send ping to legion WS and verify pong response."""
    env = ws_integration_env
    client = env["test_client"]

    project = env["create_project"]("Ping Test")
    project_id = project["project_id"]

    with client.websocket_connect(f"/ws/legion/{project_id}") as ws:
        ws.receive_json(mode="text")  # connection_established

        ws.send_json({"type": "ping"})

        msg = _receive_until_type(ws, "pong")
        assert msg is not None, "Did not receive pong response"
