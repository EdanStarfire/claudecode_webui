"""
Tests for issue #953: Use tool_use_id and agent_id from ToolPermissionContext.

SDK v0.1.52 exposes tool_use_id and agent_id on ToolPermissionContext, enabling
direct O(1) lookup instead of fragile signature-based matching.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.messages import PermissionRequestMessage, ToolCall, ToolDisplayInfo, ToolState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call(session_id: str, tool_use_id: str, name: str, input_params: dict) -> ToolCall:
    return ToolCall(
        tool_use_id=tool_use_id,
        session_id=session_id,
        name=name,
        input=input_params,
        status=ToolState.PENDING,
        created_at=time.time(),
        requires_permission=True,
        parent_tool_use_id=None,
        display=ToolDisplayInfo(
            state=ToolState.PENDING,
            visible=True,
            collapsed=False,
            style="default",
        ),
    )


def _make_coordinator(session_id: str, tool_use_id: str | None = None) -> MagicMock:
    """Build a minimal mock SessionCoordinator with both lookup methods."""
    coord = MagicMock()
    event = asyncio.Event()
    tool_calls_by_id: dict[str, ToolCall] = {}

    def get_tool_call_by_id(sid: str, tuid: str) -> ToolCall | None:
        return tool_calls_by_id.get(tuid)

    def find_tool_call_by_signature(sid: str, name: str, params: dict) -> ToolCall | None:
        # Return first match by name (simplified)
        for tc in tool_calls_by_id.values():
            if tc.name == name:
                return tc
        return None

    def get_tool_call_event(sid: str) -> asyncio.Event:
        return event

    def is_uploaded_file(sid: str, path: str) -> bool:
        return False

    coord.get_tool_call_by_id.side_effect = get_tool_call_by_id
    coord.find_tool_call_by_signature.side_effect = find_tool_call_by_signature
    coord.get_tool_call_event.side_effect = get_tool_call_event
    coord.is_uploaded_file.side_effect = is_uploaded_file
    coord._tool_calls_by_id = tool_calls_by_id
    coord._event = event
    return coord


def _make_context(tool_use_id: str | None = None, agent_id: str | None = None):
    """Create a mock ToolPermissionContext with optional fields."""
    ctx = MagicMock()
    ctx.tool_use_id = tool_use_id
    ctx.agent_id = agent_id
    ctx.suggestions = []
    return ctx


# ---------------------------------------------------------------------------
# Test 1: PermissionRequestMessage has tool_use_id and agent_id fields
# ---------------------------------------------------------------------------

def test_permission_request_message_has_new_fields():
    """PermissionRequestMessage supports tool_use_id and agent_id with None defaults."""
    msg = PermissionRequestMessage(
        request_id="req-1",
        tool_name="Edit",
        session_id="sess-1",
    )
    assert msg.tool_use_id is None
    assert msg.agent_id is None


def test_permission_request_message_fields_populated():
    """tool_use_id and agent_id are stored and serialized correctly."""
    msg = PermissionRequestMessage(
        request_id="req-2",
        tool_name="Write",
        session_id="sess-2",
        tool_use_id="tu_abc123",
        agent_id="agent_xyz",
    )
    assert msg.tool_use_id == "tu_abc123"
    assert msg.agent_id == "agent_xyz"

    d = msg.to_dict()
    assert d["tool_use_id"] == "tu_abc123"
    assert d["agent_id"] == "agent_xyz"


def test_permission_request_message_to_dict_includes_none_fields():
    """to_dict() includes tool_use_id and agent_id even when None."""
    msg = PermissionRequestMessage(request_id="req-3", tool_name="Bash", session_id="sess-3")
    d = msg.to_dict()
    assert "tool_use_id" in d
    assert "agent_id" in d
    assert d["tool_use_id"] is None
    assert d["agent_id"] is None


# ---------------------------------------------------------------------------
# Test 2: get_tool_call_by_id() O(1) lookup in SessionCoordinator
# ---------------------------------------------------------------------------

def test_get_tool_call_by_id_returns_correct_call():
    """get_tool_call_by_id() returns the exact tool call for its ID."""
    import tempfile
    from src.session_coordinator import SessionCoordinator

    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(data_dir=Path(tmp))
        session_id = "sess-lookup"

        coord.create_tool_call(
            session_id=session_id,
            tool_use_id="tu-direct-001",
            name="Read",
            input_params={"file_path": "/foo.txt"},
            requires_permission=False,
        )

        result = coord.get_tool_call_by_id(session_id, "tu-direct-001")
        assert result is not None
        assert result.tool_use_id == "tu-direct-001"
        assert result.name == "Read"


def test_get_tool_call_by_id_returns_none_for_unknown_id():
    """get_tool_call_by_id() returns None for an unknown tool_use_id."""
    import tempfile
    from src.session_coordinator import SessionCoordinator

    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(data_dir=Path(tmp))
        result = coord.get_tool_call_by_id("sess-x", "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Test 3: Permission callback extracts tool_use_id from context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_permission_callback_uses_direct_lookup_when_tool_use_id_present():
    """With tool_use_id in context, get_tool_call_by_id() is called, not signature matching."""
    session_id = "sess-direct"
    tool_use_id = "tu_edit_001"
    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, tool_use_id, "Edit", {"file_path": "/bar.py"})
    coord._tool_calls_by_id[tool_use_id] = tc

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    ctx = _make_context(tool_use_id=tool_use_id, agent_id=None)

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr_msg,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
    ):
        mock_pr_msg.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        coord.update_tool_call_permission_request = MagicMock(return_value=None)
        coord.session_manager = MagicMock()
        coord.session_manager.get_session_info = AsyncMock(
            return_value=MagicMock(current_permission_mode="default")
        )

        cb = svc.create_permission_callback(session_id)
        task = asyncio.create_task(cb("Edit", {"file_path": "/bar.py"}, ctx))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        # Direct lookup should have been called
        coord.get_tool_call_by_id.assert_called_with(session_id, tool_use_id)
        # Signature matching should NOT have been called (direct lookup succeeded)
        coord.find_tool_call_by_signature.assert_not_called()


@pytest.mark.asyncio
async def test_permission_callback_falls_back_to_signature_when_no_tool_use_id():
    """Without tool_use_id in context, signature matching is used as fallback."""
    session_id = "sess-fallback"
    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "tu_read_001", "Read", {"file_path": "/foo.txt"})
    coord._tool_calls_by_id["tu_read_001"] = tc

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    # Context without tool_use_id (older SDK behavior)
    ctx = _make_context(tool_use_id=None, agent_id=None)

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr_msg,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
    ):
        mock_pr_msg.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        coord.update_tool_call_permission_request = MagicMock(return_value=None)
        coord.session_manager = MagicMock()
        coord.session_manager.get_session_info = AsyncMock(
            return_value=MagicMock(current_permission_mode="default")
        )

        cb = svc.create_permission_callback(session_id)
        task = asyncio.create_task(cb("Read", {"file_path": "/foo.txt"}, ctx))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        # Direct lookup should NOT have been called (no tool_use_id)
        coord.get_tool_call_by_id.assert_not_called()
        # Signature matching should have been called as fallback
        coord.find_tool_call_by_signature.assert_called()


@pytest.mark.asyncio
async def test_permission_callback_extracts_agent_id_from_context():
    """agent_id from context is passed to PermissionRequestMessage."""
    session_id = "sess-agent"
    tool_use_id = "tu_bash_001"
    agent_id = "agent_subagent_42"
    coord = _make_coordinator(session_id)

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    ctx = _make_context(tool_use_id=tool_use_id, agent_id=agent_id)

    captured_kwargs = {}

    def capture_permission_request(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with (
        patch("src.permission_service.PermissionRequestMessage", side_effect=capture_permission_request),
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
    ):
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        coord.session_manager = MagicMock()
        coord.session_manager.get_session_info = AsyncMock(
            return_value=MagicMock(current_permission_mode="default")
        )

        cb = svc.create_permission_callback(session_id)
        task = asyncio.create_task(cb("Bash", {"command": "ls"}, ctx))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert captured_kwargs.get("tool_use_id") == tool_use_id
        assert captured_kwargs.get("agent_id") == agent_id


# ---------------------------------------------------------------------------
# Test 4: Direct lookup resolves instantly (no wait needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_direct_lookup_resolves_without_wait():
    """When tool_use_id is provided and tool call exists, no event wait is triggered."""
    session_id = "sess-instant"
    tool_use_id = "tu_write_001"
    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, tool_use_id, "Write", {"file_path": "/out.txt"})
    coord._tool_calls_by_id[tool_use_id] = tc

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    ctx = _make_context(tool_use_id=tool_use_id)

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr_msg,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
    ):
        mock_pr_msg.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        coord.update_tool_call_permission_request = MagicMock(return_value=None)
        coord.session_manager = MagicMock()
        coord.session_manager.get_session_info = AsyncMock(
            return_value=MagicMock(current_permission_mode="default")
        )

        cb = svc.create_permission_callback(session_id)
        task = asyncio.create_task(cb("Write", {"file_path": "/out.txt"}, ctx))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        # get_tool_call_event should not be called when direct lookup succeeds immediately
        coord.get_tool_call_event.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: Backward compat — None context still works
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_permission_callback_with_none_context():
    """Passing None as context doesn't crash; falls back to signature matching."""
    session_id = "sess-none-ctx"
    coord = _make_coordinator(session_id)

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr_msg,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
        patch("src.permission_service.asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_pr_msg.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        coord.session_manager = MagicMock()
        coord.session_manager.get_session_info = AsyncMock(
            return_value=MagicMock(current_permission_mode="default")
        )

        cb = svc.create_permission_callback(session_id)
        result = await cb("Read", {"file_path": "/x.txt"}, None)

        # Should auto-deny (no tool call found), not crash
        assert result == {"behavior": "deny"}
