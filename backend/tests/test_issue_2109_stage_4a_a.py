"""
Regression tests for issue #2109, stage 4a-A (AC1 + AC2).

AC1: restart_session must mark open tools interrupted, mirroring
terminate_session/interrupt_session — covered in test_session_coordinator.py.

AC2: the server emits a pending `tool_call` record at `content_block_start`
instead of relying on the frontend to synthesize the early card:
- claude_sdk.py's `_convert_sdk_message` stamps `tool_use_pending` — covered in
  test_claude_sdk.py.
- session_coordinator.py's `update_tool_call_input` fills in input on an
  already-pending record — covered in test_session_coordinator.py.
- web_server.py's `_create_message_callback` (assistant_delta branch) and
  `_emit_tool_call_updates` (assistant branch) wire create-or-update together —
  covered here.
"""

from __future__ import annotations

import pytest

from backend.models.messages import ToolState
from backend.web_server import BackendApp
from shared.event_queue import EventQueue


def _tool_calls(queue_events: list[dict], tool_use_id: str) -> list[dict]:
    """Extract the ordered sequence of `data` dicts broadcast for a tool_use_id,
    from the bare `tool_call` top-level envelope."""
    out = []
    for entry in queue_events:
        if entry.get("type") != "tool_call":
            continue
        data = entry.get("data", {})
        if data.get("tool_use_id") == tool_use_id:
            out.append(data)
    return out


@pytest.mark.asyncio
async def test_issue_2109_assistant_delta_with_tool_use_pending_creates_and_emits(tmp_path):
    """AC2: an assistant_delta carrying tool_use_pending (content_block_start) must
    create a pending ToolCall and emit it, while still forwarding the
    assistant_delta event unchanged."""
    session_id = "sess-2109-pending"
    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)

    message_data = {
        "type": "assistant_delta",
        "uuid": "u1",
        "session_id": session_id,
        "parent_tool_use_id": None,
        "event": {"type": "content_block_start"},
        "turn_id": "turn-1",
        "tool_use_pending": {"tool_use_id": "toolu_pend1", "name": "Bash"},
        "timestamp": 1.0,
    }

    await callback(session_id, message_data)

    events = webui.session_queues[session_id].events_since(0)[0]
    tool_calls = _tool_calls(events, "toolu_pend1")
    assert len(tool_calls) == 1
    assert tool_calls[0]["status"] == "pending"
    assert tool_calls[0]["name"] == "Bash"
    assert tool_calls[0]["input"] == {}

    # The ToolCall is tracked as active (available for the later input-fill step).
    tool_call = webui.coordinator._get_active_tool_call(session_id, "toolu_pend1")
    assert tool_call is not None
    assert tool_call.status == ToolState.PENDING

    # assistant_delta is still forwarded, unchanged.
    deltas = [e for e in events if e.get("type") == "assistant_delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["uuid"] == "u1"
    assert deltas[0]["data"]["turn_id"] == "turn-1"


@pytest.mark.asyncio
async def test_issue_2109_assistant_delta_without_tool_use_pending_creates_nothing(tmp_path):
    """A plain text-delta assistant_delta (no tool_use_pending key) must not create
    any ToolCall — only the forwarded assistant_delta event is emitted."""
    session_id = "sess-2109-no-pending"
    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)

    message_data = {
        "type": "assistant_delta",
        "uuid": "u2",
        "session_id": session_id,
        "parent_tool_use_id": None,
        "event": {"type": "content_block_delta"},
        "timestamp": 2.0,
    }

    await callback(session_id, message_data)

    events = webui.session_queues[session_id].events_since(0)[0]
    assert all(e.get("type") != "tool_call" for e in events)
    assert len(events) == 1
    assert events[0]["type"] == "assistant_delta"


@pytest.mark.asyncio
async def test_issue_2109_subagent_assistant_delta_with_tool_use_pending_creates_nothing(tmp_path):
    """Subagent streaming deltas (parent_tool_use_id set) are dropped entirely,
    matching the existing drop-and-return behavior — even if tool_use_pending is
    present, nothing is created or emitted."""
    session_id = "sess-2109-subagent"
    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)

    message_data = {
        "type": "assistant_delta",
        "uuid": "u3",
        "session_id": session_id,
        "parent_tool_use_id": "parent-tool-abc",
        "event": {"type": "content_block_start"},
        "tool_use_pending": {"tool_use_id": "toolu_sub1", "name": "Bash"},
        "timestamp": 3.0,
    }

    await callback(session_id, message_data)

    assert webui.session_queues[session_id].events_since(0)[0] == []
    assert webui.coordinator._get_active_tool_call(session_id, "toolu_sub1") is None


@pytest.mark.asyncio
async def test_issue_2109_emit_tool_call_updates_updates_existing_pending_record(tmp_path):
    """AC2: when a pending ToolCall already exists for a tool_use_id (block-start
    already ran), the assistant-message branch of _emit_tool_call_updates must call
    update_tool_call_input instead of double-creating via create_tool_call."""
    session_id = "sess-2109-update-existing"
    tool_use_id = "toolu_update1"
    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    pending_call = webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=tool_use_id,
        name="Bash",
        input_params={},
    )
    assert pending_call.input == {}

    message_data = {
        "type": "assistant",
        "timestamp": 1000.0,
        "session_id": session_id,
        "turn_id": "turn-update-1",
        "metadata": {
            "tool_uses": [{"id": tool_use_id, "name": "Bash", "input": {"command": "echo hi"}}],
        },
    }

    await webui._emit_tool_call_updates(session_id, message_data)

    # Same object, input filled in, still PENDING (update_tool_call_input doesn't
    # touch status).
    assert pending_call.input == {"command": "echo hi"}
    assert pending_call.status == ToolState.PENDING

    events = webui.session_queues[session_id].events_since(0)[0]
    tool_calls = _tool_calls(events, tool_use_id)
    assert len(tool_calls) == 1
    assert tool_calls[0]["input"] == {"command": "echo hi"}


@pytest.mark.asyncio
async def test_issue_2109_emit_tool_call_updates_falls_back_to_create_when_no_pending(tmp_path):
    """Fixture replay / non-streaming paths never see a pending record from
    content_block_start — _emit_tool_call_updates must fall back to create_tool_call
    exactly as before (no pre-existing active tool call for the id)."""
    session_id = "sess-2109-create-fresh"
    tool_use_id = "toolu_fresh1"
    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    assert webui.coordinator._get_active_tool_call(session_id, tool_use_id) is None

    message_data = {
        "type": "assistant",
        "timestamp": 1000.0,
        "session_id": session_id,
        "turn_id": "turn-fresh-1",
        "metadata": {
            "tool_uses": [{"id": tool_use_id, "name": "Read", "input": {"file_path": "/tmp/x"}}],
        },
    }

    await webui._emit_tool_call_updates(session_id, message_data)

    tool_call = webui.coordinator._get_active_tool_call(session_id, tool_use_id)
    assert tool_call is not None
    assert tool_call.name == "Read"
    assert tool_call.input == {"file_path": "/tmp/x"}
    assert tool_call.status == ToolState.PENDING

    events = webui.session_queues[session_id].events_since(0)[0]
    tool_calls = _tool_calls(events, tool_use_id)
    assert len(tool_calls) == 1
    assert tool_calls[0]["input"] == {"file_path": "/tmp/x"}
