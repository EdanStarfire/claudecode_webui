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

from backend.message_parser import MessageType, ParsedMessage
from backend.mock_sdk import ActionType, MockClaudeSDK, SessionRecording
from backend.models.messages import ToolState
from backend.web_server import BackendApp

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _tool_call_statuses(queue: list[dict], tool_use_id: str) -> list[str]:
    """Extract the ordered sequence of tool_call statuses broadcast for a tool_use_id."""
    statuses = []
    for entry in queue:
        data = entry.get("data", {})
        if data.get("type") == "tool_call" and data.get("tool_use_id") == tool_use_id:
            statuses.append(data.get("status"))
    return statuses


# ---------------------------------------------------------------------------
# Bug C: SessionRecording tracks the original permission_response message dict
# ---------------------------------------------------------------------------


class TestSessionRecordingActionMessages:
    """Unit coverage for the action_messages tracking added by issue #1964."""

    def test_action_messages_tracks_permission_response_message(self):
        """The permission_response action boundary must retain its original message
        dict (with corrected top-level fields), not just its classification."""
        recording = SessionRecording(FIXTURES_DIR / "permission_flow")

        assert recording.get_action_count() == 2
        assert recording.get_expected_action(0) == ActionType.USER_MESSAGE
        assert recording.get_expected_action(1) == ActionType.PERMISSION_ALLOW

        response_msg = recording.get_action_message(1)
        assert response_msg is not None
        assert response_msg.get("type") == "permission_response"
        assert response_msg.get("decision") == "allow"
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
    webui.session_queues[session_id] = []

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

    statuses = _tool_call_statuses(webui.session_queues[session_id], "toolu_perm01")
    assert statuses == ["pending", "awaiting_permission", "running", "completed"], (
        f"Expected full unified ToolCall lifecycle from mock replay, got {statuses}"
    )


# ---------------------------------------------------------------------------
# Bug B: deny path (narrower unit test, no new fixture needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1964_permission_response_deny_transitions_tool_call_to_denied(tmp_path):
    """A synthetic permission_response ParsedMessage with decision=deny, applied
    directly via _emit_tool_call_updates against a pre-seeded PENDING ToolCall,
    must transition it to denied."""
    session_id = "sess-1964-deny"
    tool_use_id = "toolu_deny01"

    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = []

    tool_call = webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=tool_use_id,
        name="Bash",
        input_params={"command": "rm -rf /"},
        requires_permission=True,
    )
    assert tool_call.status == ToolState.PENDING

    parsed_message = ParsedMessage(
        type=MessageType.PERMISSION_RESPONSE,
        timestamp=1000.0,
        session_id=session_id,
        metadata={
            "tool_use_id": tool_use_id,
            "tool_name": "Bash",
            "decision": "deny",
            "request_id": "perm-req-deny",
        },
    )

    await webui._emit_tool_call_updates(session_id, parsed_message)

    assert tool_call.status == ToolState.DENIED
    statuses = _tool_call_statuses(webui.session_queues[session_id], tool_use_id)
    assert statuses == ["denied"]


@pytest.mark.asyncio
async def test_issue_1964_permission_response_missing_tool_use_id_falls_back_to_unique_awaiting(tmp_path):
    """A permission_response lacking tool_use_id (e.g. an older/malformed fixture)
    must still resolve when exactly one tool call in the session is awaiting
    permission — signature-matching can't help here since PermissionResponseHandler
    never populates metadata['input_params']."""
    session_id = "sess-1964-fallback"
    tool_use_id = "toolu_fallback01"

    webui = BackendApp(data_dir=tmp_path)
    webui.session_queues[session_id] = []

    tool_call = webui.coordinator.create_tool_call(
        session_id=session_id,
        tool_use_id=tool_use_id,
        name="Edit",
        input_params={"file_path": "/tmp/test.txt"},
        requires_permission=True,
    )
    from backend.models.messages import PermissionInfo

    webui.coordinator.update_tool_call_permission_request(
        session_id, tool_use_id, PermissionInfo(message="Allow Edit?")
    )
    assert tool_call.status == ToolState.AWAITING_PERMISSION

    parsed_message = ParsedMessage(
        type=MessageType.PERMISSION_RESPONSE,
        timestamp=1000.0,
        session_id=session_id,
        metadata={
            "tool_name": "Edit",
            "decision": "allow",
            "request_id": "perm-req-fallback",
            # tool_use_id deliberately omitted
        },
    )

    await webui._emit_tool_call_updates(session_id, parsed_message)

    assert tool_call.status == ToolState.RUNNING
    statuses = _tool_call_statuses(webui.session_queues[session_id], tool_use_id)
    assert statuses == ["running"]
