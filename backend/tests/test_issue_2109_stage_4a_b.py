"""
Regression tests for issue #2109, stage 4a-B (AC6 + AC9).

AC6: permission linkage by tool_use_id / request_id only — no name+status
signature-matching fallback. Covers:
- ToolCall.request_id to_dict()/from_dict() round-trip.
- SessionCoordinator.update_tool_call_permission_request() sets request_id.
- SessionCoordinator.get_tool_call_by_request_id() disambiguation among
  several active tool calls (two concurrent same-name tools, each with its
  own request_id).
- permission_service.py request phase auto-denies (no polling) when
  tool_use_id is absent, and response phase falls back to request_id —
  covered in test_issue_953_tool_use_id_matching.py and
  test_issue_858_permission_race.py respectively.
- web_server.py fixture-mirror envelope byte-equivalence with the live path —
  covered in test_issue_1964_permission_flow_replay.py.
"""

from __future__ import annotations

import time

import pytest

from backend.models.messages import PermissionInfo, ToolCall, ToolDisplayInfo, ToolState


def _make_tool_call(session_id: str, tool_use_id: str, name: str) -> ToolCall:
    return ToolCall(
        tool_use_id=tool_use_id,
        session_id=session_id,
        name=name,
        input={},
        status=ToolState.PENDING,
        created_at=time.time(),
        requires_permission=True,
        display=ToolDisplayInfo(state=ToolState.PENDING, visible=True, collapsed=False, style="default"),
    )


# ---------------------------------------------------------------------------
# ToolCall.request_id serialization
# ---------------------------------------------------------------------------


def test_tool_call_request_id_round_trip():
    tc = _make_tool_call("sess-a", "tu_1", "Edit")
    tc.request_id = "req-123"
    data = tc.to_dict()
    assert data["request_id"] == "req-123"

    restored = ToolCall.from_dict(data)
    assert restored.request_id == "req-123"


def test_tool_call_request_id_omitted_when_none():
    tc = _make_tool_call("sess-a", "tu_1", "Edit")
    data = tc.to_dict()
    assert "request_id" not in data

    restored = ToolCall.from_dict(data)
    assert restored.request_id is None


def test_tool_call_with_status_update_preserves_request_id():
    tc = _make_tool_call("sess-a", "tu_1", "Edit")
    tc.request_id = "req-123"
    updated = tc.with_status_update(status=ToolState.RUNNING)
    assert updated.request_id == "req-123"


# ---------------------------------------------------------------------------
# SessionCoordinator.update_tool_call_permission_request() sets request_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_tool_call_permission_request_sets_request_id(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2109-b-request-id"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Edit",
        input_params={"file_path": "/x.py"},
        requires_permission=True,
    )

    updated = coord.update_tool_call_permission_request(
        session_id, "tu-001", PermissionInfo(message="Allow Edit?"), request_id="req-abc"
    )

    assert updated is not None
    assert updated.request_id == "req-abc"
    assert coord.get_tool_call_by_id(session_id, "tu-001").request_id == "req-abc"


@pytest.mark.asyncio
async def test_update_tool_call_permission_request_defaults_request_id_to_none(tmp_path):
    """Existing call sites that don't pass request_id must be unaffected."""
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2109-b-default"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-002",
        name="Edit",
        input_params={"file_path": "/x.py"},
        requires_permission=True,
    )

    updated = coord.update_tool_call_permission_request(
        session_id, "tu-002", PermissionInfo(message="Allow Edit?")
    )

    assert updated.request_id is None


# ---------------------------------------------------------------------------
# SessionCoordinator.get_tool_call_by_request_id()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tool_call_by_request_id_disambiguates_concurrent_same_name_tools(tmp_path):
    """The actual T4 scenario: two concurrent same-name tools in one session,
    each with its own request_id — must resolve to the right one, which
    signature-matching (name + first input param) could not guarantee."""
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2109-b-concurrent"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-edit-1",
        name="Edit",
        input_params={"file_path": "/same.py"},
        requires_permission=True,
    )
    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-edit-2",
        name="Edit",
        input_params={"file_path": "/same.py"},
        requires_permission=True,
    )

    coord.update_tool_call_permission_request(
        session_id, "tu-edit-1", PermissionInfo(message="Allow Edit?"), request_id="req-1"
    )
    coord.update_tool_call_permission_request(
        session_id, "tu-edit-2", PermissionInfo(message="Allow Edit?"), request_id="req-2"
    )

    found_1 = coord.get_tool_call_by_request_id(session_id, "req-1")
    found_2 = coord.get_tool_call_by_request_id(session_id, "req-2")

    assert found_1.tool_use_id == "tu-edit-1"
    assert found_2.tool_use_id == "tu-edit-2"


@pytest.mark.asyncio
async def test_get_tool_call_by_request_id_returns_none_when_absent(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2109-b-absent"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Edit",
        input_params={"file_path": "/x.py"},
        requires_permission=True,
    )

    assert coord.get_tool_call_by_request_id(session_id, "nonexistent-request") is None


@pytest.mark.asyncio
async def test_get_tool_call_by_request_id_ignores_resolved_tool_call(tmp_path):
    """request_id is never cleared off the ToolCall once set (it persists through
    running/completed). A duplicate/replayed permission_response sharing a stale
    request_id must not re-resolve a tool call that already moved past
    AWAITING_PERMISSION — that would let a second, late decision flip an
    already-running (or already-completed) tool back to denied."""
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2109-b-stale-request-id"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Edit",
        input_params={"file_path": "/x.py"},
        requires_permission=True,
    )
    coord.update_tool_call_permission_request(
        session_id, "tu-001", PermissionInfo(message="Allow Edit?"), request_id="req-stale"
    )
    # First (real) response resolves it to RUNNING — request_id is left in place.
    coord.update_tool_call_permission_response(session_id, "tu-001", granted=True)
    assert coord.get_tool_call_by_id(session_id, "tu-001").status == ToolState.RUNNING

    # A second, duplicate/replayed response for the same request_id must not
    # find this tool call anymore.
    assert coord.get_tool_call_by_request_id(session_id, "req-stale") is None


def test_find_tool_call_by_signature_removed():
    """Issue #2109 (AC6): the name+status signature-matching fallback is gone —
    no production call sites remain, and the method itself no longer exists."""
    from backend.session_coordinator import SessionCoordinator

    assert not hasattr(SessionCoordinator, "find_tool_call_by_signature")
