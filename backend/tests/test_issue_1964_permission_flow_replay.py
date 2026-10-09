"""
Regression tests for issue #1964.

The mock-SDK replay harness never surfaced awaiting_permission tool state for the
`permission_flow` fixture. Three compounding bugs, fixed together:

- Bug A: `permission_flow/messages.jsonl` nested its business fields (tool_name,
  input_params, request_id, tool_use_id, decision) under a "metadata" sub-object,
  but PermissionRequestHandler/PermissionResponseHandler (backend/message_parser.py)
  read those fields from the top level of the message dict.
- Bug B: `BackendApp._emit_tool_call_updates()` (backend/web_server.py) had no
  branch for permission_request/permission_response, so a replayed tool went
  straight from PENDING to COMPLETED, skipping AWAITING_PERMISSION/RUNNING/DENIED.
- Bug C: `SessionRecording._build_segments()` (backend/mock_sdk.py) consumed a
  permission_response message purely as an action-boundary marker — the message
  dict itself was discarded, so `MockClaudeSDK._handle_pending_permissions()`
  never replayed the scripted decision even after fixing A and B.

These tests drive the real replay pipeline end-to-end (MockClaudeSDK ->
SessionCoordinator._create_message_callback -> BackendApp's own callback ->
_emit_tool_call_updates), plus narrower unit coverage for the pieces above.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.mock_sdk import ActionType, MockClaudeSDK, SessionRecording
from backend.models.messages import MessageRecord, PermissionInfo, ToolState
from backend.web_server import BackendApp
from shared.event_queue import EventQueue

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _tool_call_statuses(queue: list[dict], tool_use_id: str) -> list[str]:
    """Extract the ordered sequence of tool_call statuses broadcast for a tool_use_id,
    from the bare `tool_call` top-level envelope — the only shape emitted since #2065
    stage 2b-C deleted the 2a-C shim."""
    statuses = []
    for entry in queue:
        if entry.get("type") != "tool_call":
            continue
        data = entry.get("data", {})
        if data.get("tool_use_id") == tool_use_id:
            statuses.append(data.get("status"))
    return statuses


# ---------------------------------------------------------------------------
# Bug C: SessionRecording tracks the original permission_response message dict
# ---------------------------------------------------------------------------


class TestSessionRecordingActionMessages:
    """Unit coverage for the action_messages tracking added by issue #1964."""

    def test_action_messages_tracks_permission_response_message(self):
        """The permission_response action boundary must retain its original message
        dict (with corrected top-level fields), not just its classification.

        Issue #2109 (AC11): permission_flow's permission_request/permission_response
        pair is now reconstructed as real `tool_call` transitions — the action
        boundary is the `running` transition (`permission_granted: True`), not a
        standalone `permission_response`-typed record."""
        recording = SessionRecording(FIXTURES_DIR / "permission_flow")

        assert recording.get_action_count() == 2
        assert recording.get_expected_action(0) == ActionType.USER_MESSAGE
        assert recording.get_expected_action(1) == ActionType.PERMISSION_ALLOW

        response_msg = recording.get_action_message(1)
        assert response_msg is not None
        assert response_msg.get("type") == "tool_call"
        assert response_msg.get("status") == "running"
        assert response_msg.get("permission_granted") is True
        assert response_msg.get("tool_use_id") == "toolu_perm01"

    def test_get_action_message_out_of_range_returns_none(self):
        recording = SessionRecording(FIXTURES_DIR / "permission_flow")
        assert recording.get_action_message(999) is None


# ---------------------------------------------------------------------------
# Bug A + B + C: end-to-end replay through the real production pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1964_permission_flow_replay_full_lifecycle(tmp_path):
    """Replaying the permission_flow fixture through the real coordinator +
    BackendApp pipeline must surface the full unified ToolCall lifecycle —
    pending -> awaiting_permission -> running -> completed — not skip straight
    from pending to completed (the bug reported in #1959/#1964)."""
    session_id = "sess-1964-e2e"

    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    coordinator = webui.coordinator
    coordinator.add_message_callback(session_id, webui._create_message_callback(session_id))
    sdk_callback = coordinator._create_message_callback(session_id)

    mock = MockClaudeSDK(
        session_id=session_id,
        working_directory=str(tmp_path),
        session_dir=str(FIXTURES_DIR / "permission_flow"),
        message_callback=sdk_callback,
        # Only needs to be truthy so ReplayEngine.replay_segment fires message_callback
        # for the permission_request message — production's real permission_callback
        # (permission_service.py) is never reached by the mock replay path.
        permission_callback=lambda *args, **kwargs: None,
        speed_factor=0.0,
    )

    assert await mock.start()
    assert await mock.send_message("Edit the file at /tmp/test.txt")

    statuses = _tool_call_statuses(webui.session_queues[session_id].events_since(0)[0], "toolu_perm01")
    assert statuses == ["pending", "awaiting_permission", "running", "completed"], (
        f"Expected full unified ToolCall lifecycle from mock replay, got {statuses}"
    )


# ---------------------------------------------------------------------------
# Bug B: deny path (narrower unit test, no new fixture needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1964_permission_response_deny_transitions_tool_call_to_denied(tmp_path):
    """A synthetic permission_response message dict with decision=deny, applied
    directly via _emit_tool_call_updates against a pre-seeded PENDING ToolCall,
    must transition it to denied."""
    session_id = "sess-1964-deny"
    tool_use_id = "toolu_deny01"

    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    tool_call = webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=tool_use_id,
        name="Bash",
        input_params={"command": "rm -rf /"},
        requires_permission=True,
    )
    assert tool_call.status == ToolState.PENDING

    # Issue #2084 (stage 3-B, §4): _emit_tool_call_updates() now reads the canonical
    # dict shape directly (mechanical ParsedMessage -> dict signature change).
    message_data = {
        "type": "permission_response",
        "timestamp": 1000.0,
        "session_id": session_id,
        "metadata": {
            "tool_use_id": tool_use_id,
            "tool_name": "Bash",
            "decision": "deny",
            "request_id": "perm-req-deny",
        },
    }

    await webui._emit_tool_call_updates(session_id, message_data)

    assert tool_call.status == ToolState.DENIED
    statuses = _tool_call_statuses(webui.session_queues[session_id].events_since(0)[0], tool_use_id)
    assert statuses == ["denied"]


@pytest.mark.asyncio
async def test_issue_1964_permission_response_missing_tool_use_id_resolves_by_request_id(tmp_path):
    """Issue #2109 (AC6): a permission_response lacking tool_use_id (e.g. an
    older/malformed fixture) resolves via the ToolCall's request_id (set when it
    transitioned to awaiting_permission) — not the deleted name+status fallback."""
    session_id = "sess-1964-fallback"
    tool_use_id = "toolu_fallback01"
    request_id = "perm-req-fallback"

    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    tool_call = webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=tool_use_id,
        name="Edit",
        input_params={"file_path": "/tmp/test.txt"},
        requires_permission=True,
    )
    webui.coordinator.update_tool_call_permission_request(
        session_id, tool_use_id, PermissionInfo(message="Allow Edit?"), request_id=request_id
    )
    assert tool_call.status == ToolState.AWAITING_PERMISSION

    # Issue #2084 (stage 3-B, §4): _emit_tool_call_updates() now reads the canonical
    # dict shape directly (mechanical ParsedMessage -> dict signature change).
    message_data = {
        "type": "permission_response",
        "timestamp": 1000.0,
        "session_id": session_id,
        "metadata": {
            "tool_name": "Edit",
            "decision": "allow",
            "request_id": request_id,
            # tool_use_id deliberately omitted
        },
    }

    await webui._emit_tool_call_updates(session_id, message_data)

    assert tool_call.status == ToolState.RUNNING
    statuses = _tool_call_statuses(webui.session_queues[session_id].events_since(0)[0], tool_use_id)
    assert statuses == ["running"]


# ---------------------------------------------------------------------------
# Issue #2109 (AC9): fixture-mirror envelope byte-equivalence with the live path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_2109_fixture_mirror_envelope_matches_live_path_shape(tmp_path):
    """The fixture-mirror permission_request/permission_response branches in
    `BackendApp._emit_tool_call_updates()` must build their envelope the same way the
    live path (permission_service.py) does — via `MessageRecord.from_tool_call()` —
    not a raw `to_dict()` plus manual `type`/`request_id` stamping. Compares the
    mirror's emitted envelope key set directly against a live-path equivalent built
    for the same ToolCall transition."""
    session_id = "sess-2109-ac9"
    live_tool_use_id = "toolu_ac9_live"
    mirror_tool_use_id = "toolu_ac9_mirror"
    request_id = "perm-req-ac9"

    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = EventQueue()

    # Live-path equivalent: exactly what permission_service.py's request phase builds.
    webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=live_tool_use_id,
        name="Edit",
        input_params={"file_path": "/x.py"},
        requires_permission=True,
    )
    updated_live = webui.coordinator.update_tool_call_permission_request(
        session_id, live_tool_use_id, PermissionInfo(message="Allow Edit?"), request_id=request_id
    )
    live_envelope = MessageRecord.from_tool_call(updated_live).to_dict()

    # Mirror path: fixture replay through _emit_tool_call_updates()'s permission_request branch.
    webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=mirror_tool_use_id,
        name="Edit",
        input_params={"file_path": "/x.py"},
        requires_permission=True,
    )
    mirror_message_data = {
        "type": "permission_request",
        "metadata": {
            "tool_use_id": mirror_tool_use_id,
            "tool_name": "Edit",
            "request_id": request_id,
        },
    }
    await webui._emit_tool_call_updates(session_id, mirror_message_data)
    queue, _, _ = webui.session_queues[session_id].events_since(0)
    mirror_envelope = next(
        entry["data"] for entry in queue
        if entry.get("type") == "tool_call" and entry["data"].get("tool_use_id") == mirror_tool_use_id
    )

    # Same shape: both constructed via MessageRecord.from_tool_call(), not independently.
    assert set(live_envelope.keys()) == set(mirror_envelope.keys())
    assert live_envelope["type"] == mirror_envelope["type"] == "tool_call"
    # request_id comes from ToolCall.request_id via to_dict() on both paths, not a
    # manual stamp — equal because both transitions used the same request_id.
    assert live_envelope["request_id"] == mirror_envelope["request_id"] == request_id
    # message_id is present on both (MessageRecord.from_tool_call always mints one) —
    # the exact AC9 gap (raw to_dict() + manual stamping) never carried this field.
    assert live_envelope["message_id"]
    assert mirror_envelope["message_id"]
