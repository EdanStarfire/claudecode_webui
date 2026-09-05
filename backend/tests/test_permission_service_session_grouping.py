"""
Regression tests for issue #1746 (stage: backend) — per-session permission grouping.

Resolving one agent's permission request must not clear another agent's
still-open "needs attention" PAUSED state on the same session. Session
PAUSED->ACTIVE must be driven by "are zero requests still open for this
session," not "did *a* request resolve."
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.messages import ToolCall, ToolDisplayInfo, ToolState
from backend.session_manager import SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call(session_id: str, name: str, input_params: dict) -> ToolCall:
    return ToolCall(
        tool_use_id=f"tu_{name}",
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


def _make_coordinator() -> MagicMock:
    """Minimal mock SessionCoordinator: tool calls resolve immediately (no event.wait())."""
    coord = MagicMock()
    tool_calls: dict[str, ToolCall] = {}

    coord.find_tool_call_by_signature.side_effect = (
        lambda sid, name, params: tool_calls.get(name)
    )
    coord.is_uploaded_file.side_effect = lambda sid, path: False
    coord.update_tool_call_permission_request = MagicMock(return_value=None)
    coord._tool_calls = tool_calls

    # Track per-session state so update_session_state's effect is visible to
    # a later get_session_info call — mirrors the real SessionManager closely
    # enough that _maybe_resume_session's "already ACTIVE" guard behaves the
    # same as in production (a redundant resume attempt is a real no-op, not
    # just an unasserted mock call).
    session_states: dict[str, SessionState] = {}

    async def _get_session_info(sid):
        return MagicMock(state=session_states.get(sid, SessionState.PAUSED))

    async def _update_session_state(sid, state):
        session_states[sid] = state

    coord.session_manager = MagicMock()
    coord.session_manager.pause_session = AsyncMock()
    coord.session_manager.update_session_state = AsyncMock(side_effect=_update_session_state)
    coord.session_manager.get_session_info = AsyncMock(side_effect=_get_session_info)
    coord._session_states = session_states
    return coord


# ---------------------------------------------------------------------------
# Test 1: two concurrent requests on the same session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_concurrent_requests_same_session_only_resumes_after_both_resolve():
    """Resolving the first of two concurrent requests must leave the session
    PAUSED; only resolving the second flips it to ACTIVE."""
    session_id = "sess-concurrent-grouping"
    coord = _make_coordinator()
    coord._tool_calls["Read"] = _make_tool_call(session_id, "Read", {"file_path": "/a.txt"})
    coord._tool_calls["Edit"] = _make_tool_call(session_id, "Edit", {"file_path": "/b.py"})

    with (
        patch("backend.permission_service.PermissionRequestMessage"),
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
    ):
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        mock_sm.from_permission_response.return_value = MagicMock(to_dict=lambda: {})

        from backend.permission_service import PermissionService

        svc = PermissionService(coordinator=coord, session_queues={})
        cb = svc.create_permission_callback(session_id)

        task1 = asyncio.create_task(cb("Read", {"file_path": "/a.txt"}, None))
        task2 = asyncio.create_task(cb("Edit", {"file_path": "/b.py"}, None))

        # Let both callbacks reach the "await permission_future" point.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if svc.open_permission_count(session_id) == 2:
                break

        assert svc.open_permission_count(session_id) == 2
        # Only one pause_session call for two concurrent requests on the same session.
        coord.session_manager.pause_session.assert_awaited_once_with(session_id)

        request_ids = list(svc.pending_by_session[session_id])
        assert len(request_ids) == 2

        # Resolve the first — session must still show as needing the second's decision.
        svc.resolve(request_ids[0], {"behavior": "allow"})
        for _ in range(20):
            await asyncio.sleep(0.01)
            if svc.open_permission_count(session_id) == 1:
                break

        assert svc.open_permission_count(session_id) == 1
        coord.session_manager.update_session_state.assert_not_called()

        # Resolve the second — now the session should resume.
        svc.resolve(request_ids[1], {"behavior": "allow"})
        await task1
        await task2

        assert svc.open_permission_count(session_id) == 0
        coord.session_manager.update_session_state.assert_awaited_once_with(
            session_id, SessionState.ACTIVE
        )
        assert session_id not in svc.pending_by_session


# ---------------------------------------------------------------------------
# Test 2: single request — unchanged regression behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_request_still_resumes_session_on_resolve():
    """Single permission request must still flip the session back to ACTIVE."""
    session_id = "sess-single-grouping"
    coord = _make_coordinator()
    coord._tool_calls["Bash"] = _make_tool_call(session_id, "Bash", {"command": "ls"})

    with (
        patch("backend.permission_service.PermissionRequestMessage"),
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
    ):
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        mock_sm.from_permission_response.return_value = MagicMock(to_dict=lambda: {})

        from backend.permission_service import PermissionService

        svc = PermissionService(coordinator=coord, session_queues={})
        cb = svc.create_permission_callback(session_id)

        task = asyncio.create_task(cb("Bash", {"command": "ls"}, None))
        for _ in range(20):
            await asyncio.sleep(0.01)
            if svc.open_permission_count(session_id) == 1:
                break

        coord.session_manager.pause_session.assert_awaited_once_with(session_id)
        request_id = next(iter(svc.pending_by_session[session_id]))

        svc.resolve(request_id, {"behavior": "allow"})
        await task

        coord.session_manager.update_session_state.assert_awaited_once_with(
            session_id, SessionState.ACTIVE
        )
        assert session_id not in svc.pending_by_session


# ---------------------------------------------------------------------------
# Test 3: cleanup_pending_for_session clears the per-session set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_pending_for_session_clears_session_set():
    """cleanup_pending_for_session must clear pending_by_session along with the futures."""
    coord = _make_coordinator()
    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    future = asyncio.get_running_loop().create_future()
    svc.pending_permissions["req-1"] = future
    svc.pending_by_session["sess-a"] = {"req-1"}

    svc.cleanup_pending_for_session("sess-a")

    assert "sess-a" not in svc.pending_by_session
    assert svc.open_permission_count("sess-a") == 0
    assert future.done()
    assert future.result()["behavior"] == "deny"


# ---------------------------------------------------------------------------
# Test 3b: cleanup_pending_for_session is scoped to the terminating session
# (issue #1754 — was iterating the global pending_permissions dict and
# auto-denying every active session's in-flight requests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_pending_for_session_does_not_touch_other_sessions():
    """Terminating session A must deny only A's own pending requests, leaving
    session B's untouched in both pending_permissions and pending_by_session."""
    coord = _make_coordinator()
    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    future_a = asyncio.get_running_loop().create_future()
    future_b = asyncio.get_running_loop().create_future()
    svc.pending_permissions["req-a"] = future_a
    svc.pending_permissions["req-b"] = future_b
    svc.pending_by_session["A"] = {"req-a"}
    svc.pending_by_session["B"] = {"req-b"}

    svc.cleanup_pending_for_session("A")

    assert future_a.done()
    assert future_a.result()["behavior"] == "deny"
    assert not future_b.done()
    assert "A" not in svc.pending_by_session
    assert "B" in svc.pending_by_session
    assert "req-b" in svc.pending_permissions


@pytest.mark.asyncio
async def test_cleanup_pending_for_session_multiple_requests_within_session():
    """All of the terminating session's own requests are denied; an unrelated
    session's request is untouched."""
    coord = _make_coordinator()
    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    future_a1 = asyncio.get_running_loop().create_future()
    future_a2 = asyncio.get_running_loop().create_future()
    future_c = asyncio.get_running_loop().create_future()
    svc.pending_permissions["req-a1"] = future_a1
    svc.pending_permissions["req-a2"] = future_a2
    svc.pending_permissions["req-c"] = future_c
    svc.pending_by_session["A"] = {"req-a1", "req-a2"}
    svc.pending_by_session["C"] = {"req-c"}

    svc.cleanup_pending_for_session("A")

    assert future_a1.done() and future_a1.result()["behavior"] == "deny"
    assert future_a2.done() and future_a2.result()["behavior"] == "deny"
    assert not future_c.done()
    assert "A" not in svc.pending_by_session
    assert "C" in svc.pending_by_session
    assert svc.pending_by_session["C"] == {"req-c"}


@pytest.mark.asyncio
async def test_cleanup_pending_for_session_no_pending_is_a_noop():
    """Terminating a session with zero pending permissions must not raise and
    must not affect another session's pending request."""
    coord = _make_coordinator()
    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    future_b = asyncio.get_running_loop().create_future()
    svc.pending_permissions["req-b"] = future_b
    svc.pending_by_session["B"] = {"req-b"}

    svc.cleanup_pending_for_session("empty-session")

    assert not future_b.done()
    assert "B" in svc.pending_by_session
    assert "empty-session" not in svc.pending_by_session


# ---------------------------------------------------------------------------
# Test 4: two different sessions never interact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_different_sessions_do_not_interact():
    """Resolving session A's request must not touch session B's open set,
    even on one shared PermissionService instance (the real deployment shape:
    one PermissionService serving every session)."""
    coord = _make_coordinator()
    session_a, session_b = "sess-a-isolated", "sess-b-isolated"
    coord._tool_calls["Read"] = _make_tool_call(session_a, "Read", {"file_path": "/a.txt"})
    coord._tool_calls["Write"] = _make_tool_call(session_b, "Write", {"file_path": "/b.txt"})

    with (
        patch("backend.permission_service.PermissionRequestMessage"),
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
    ):
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        mock_sm.from_permission_response.return_value = MagicMock(to_dict=lambda: {})

        from backend.permission_service import PermissionService

        svc = PermissionService(coordinator=coord, session_queues={})
        cb_a = svc.create_permission_callback(session_a)
        cb_b = svc.create_permission_callback(session_b)

        task_a = asyncio.create_task(cb_a("Read", {"file_path": "/a.txt"}, None))
        task_b = asyncio.create_task(cb_b("Write", {"file_path": "/b.txt"}, None))

        for _ in range(20):
            await asyncio.sleep(0.01)
            if svc.open_permission_count(session_a) == 1 and svc.open_permission_count(session_b) == 1:
                break

        req_id_a = next(iter(svc.pending_by_session[session_a]))
        svc.resolve(req_id_a, {"behavior": "allow"})
        await task_a

        # Session A resumes on its own resolution; session B's open set (and
        # state) is untouched by it.
        coord.session_manager.update_session_state.assert_awaited_once_with(
            session_a, SessionState.ACTIVE
        )
        assert session_b in svc.pending_by_session
        assert svc.open_permission_count(session_b) == 1

        req_id_b = next(iter(svc.pending_by_session[session_b]))
        svc.resolve(req_id_b, {"behavior": "allow"})
        await task_b

        assert coord.session_manager.update_session_state.await_count == 2
        coord.session_manager.update_session_state.assert_any_await(
            session_b, SessionState.ACTIVE
        )


# ---------------------------------------------------------------------------
# Test 5: cancellation while awaiting still releases the per-session open set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_await_still_releases_pending_by_session():
    """asyncio.CancelledError during `await permission_future` is a BaseException,
    not caught by `except Exception` — without a `finally`, cancellation (e.g.
    abrupt shutdown while a request is outstanding) would leak the request's
    entry in pending_by_session and could leave the session stuck PAUSED forever."""
    session_id = "sess-cancelled"
    coord = _make_coordinator()
    coord._tool_calls["Read"] = _make_tool_call(session_id, "Read", {"file_path": "/a.txt"})

    with (
        patch("backend.permission_service.PermissionRequestMessage"),
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
    ):
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})

        from backend.permission_service import PermissionService

        svc = PermissionService(coordinator=coord, session_queues={})
        cb = svc.create_permission_callback(session_id)

        task = asyncio.create_task(cb("Read", {"file_path": "/a.txt"}, None))
        for _ in range(20):
            await asyncio.sleep(0.01)
            if svc.open_permission_count(session_id) == 1:
                break

        assert svc.open_permission_count(session_id) == 1
        coord.session_manager.pause_session.assert_awaited_once_with(session_id)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert svc.open_permission_count(session_id) == 0
        assert session_id not in svc.pending_by_session
        coord.session_manager.update_session_state.assert_awaited_once_with(
            session_id, SessionState.ACTIVE
        )
