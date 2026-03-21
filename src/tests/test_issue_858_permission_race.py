"""
Regression tests for issue #858.

Replace permission callback polling loop with asyncio.Event-based synchronisation.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.messages import ToolCall, ToolDisplayInfo, ToolState

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


def _make_coordinator(session_id: str) -> MagicMock:
    """Build a minimal mock SessionCoordinator backed by a real asyncio.Event."""
    coord = MagicMock()
    event = asyncio.Event()
    tool_calls: dict[str, ToolCall] = {}

    def get_tool_call_event(sid: str) -> asyncio.Event:
        return event

    def find_tool_call_by_signature(sid: str, name: str, params: dict):
        return tool_calls.get(name)

    def is_uploaded_file(sid: str, path: str) -> bool:
        return False

    coord.get_tool_call_event.side_effect = get_tool_call_event
    coord.find_tool_call_by_signature.side_effect = find_tool_call_by_signature
    coord.is_uploaded_file.side_effect = is_uploaded_file
    coord._tool_calls = tool_calls
    coord._event = event
    return coord


# ---------------------------------------------------------------------------
# Test 1: tool call already present — no wait needed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_858_immediate_find():
    """Tool call already in _active_tool_calls when permission fires — no event.wait()."""
    session_id = "sess-immediate"
    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Read", {"file_path": "/foo.txt"})
    coord._tool_calls["Read"] = tc

    mock_session_info = MagicMock()
    mock_session_info.current_permission_mode = "default"
    coord.session_manager = MagicMock()

    with (
        patch("src.permission_service.PermissionRequestMessage"),
        patch("src.permission_service.StoredMessage"),
        patch("src.permission_service.PermissionInfo"),
    ):
        from src.permission_service import PermissionService

        svc = PermissionService(coordinator=coord, session_queues={})

        # Inject a mock future so the callback blocks awaiting user response
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        svc.pending_permissions["__any__"] = future

        cb = svc.create_permission_callback(session_id)

        # Run callback; cancel after it reaches the future-await point
        task = asyncio.create_task(cb("Read", {"file_path": "/foo.txt"}, None))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        # find_tool_call_by_signature must have been called at least once
        coord.find_tool_call_by_signature.assert_called()
        # get_tool_call_event should NOT have been called (tool was found immediately)
        coord.get_tool_call_event.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: race condition resolved via event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_858_race_condition_resolved():
    """find() returns None initially; tool call arrives 50ms later via event signal."""
    session_id = "sess-race"
    coord = _make_coordinator(session_id)

    call_count = 0
    tc = _make_tool_call(session_id, "Edit", {"file_path": "/bar.py"})

    def find_side_effect(sid, name, params):
        nonlocal call_count
        call_count += 1
        return coord._tool_calls.get(name)

    coord.find_tool_call_by_signature.side_effect = find_side_effect

    async def register_tool_call_after_delay():
        await asyncio.sleep(0.05)
        coord._tool_calls["Edit"] = tc
        coord._event.set()

    asyncio.create_task(register_tool_call_after_delay())

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

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

        start = asyncio.get_event_loop().time()
        cb = svc.create_permission_callback(session_id)

        task = asyncio.create_task(cb("Edit", {"file_path": "/bar.py"}, None))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        elapsed = asyncio.get_event_loop().time() - start

        assert call_count >= 2, "Expected at least one retry after event signal"
        assert elapsed < 1.0, "Should resolve well under 1 second"


# ---------------------------------------------------------------------------
# Test 3: multiple concurrent permission callbacks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_858_multiple_concurrent_permissions():
    """3 permission callbacks fire before any ToolCall is stored; all resolve."""
    session_id = "sess-concurrent"
    coord = _make_coordinator(session_id)

    tool_names = ["Read", "Edit", "Write"]
    tool_params = [
        {"file_path": "/a.txt"},
        {"file_path": "/b.py"},
        {"file_path": "/c.md"},
    ]
    tool_call_map = {
        name: _make_tool_call(session_id, name, params)
        for name, params in zip(tool_names, tool_params, strict=True)
    }

    async def add_tools():
        await asyncio.sleep(0.1)
        for name, tc in tool_call_map.items():
            coord._tool_calls[name] = tc
            coord._event.set()
            coord._event.clear()
            coord._event.set()

    asyncio.create_task(add_tools())

    results = {}

    from src.permission_service import PermissionService

    async def run_callback(name, params):
        svc = PermissionService(coordinator=coord, session_queues={})
        with (
            patch("src.permission_service.PermissionRequestMessage") as mock_pr,
            patch("src.permission_service.StoredMessage") as mock_sm,
            patch("src.permission_service.PermissionInfo"),
        ):
            mock_pr.return_value = MagicMock()
            mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
            coord.session_manager = MagicMock()
            coord.session_manager.get_session_info = AsyncMock(
                return_value=MagicMock(current_permission_mode="default")
            )
            coord.update_tool_call_permission_request = MagicMock(return_value=None)

            cb = svc.create_permission_callback(session_id)
            task = asyncio.create_task(cb(name, params, None))
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        results[name] = coord._tool_calls.get(name) is not None

    await asyncio.gather(
        *[run_callback(n, p) for n, p in zip(tool_names, tool_params, strict=True)]
    )

    for name in tool_names:
        assert results[name], f"Tool call {name} should have been found"


# ---------------------------------------------------------------------------
# Test 4: timeout auto-deny
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_858_timeout_auto_deny():
    """find() always returns None; callback auto-denies when event.wait() times out."""
    session_id = "sess-timeout"
    coord = _make_coordinator(session_id)
    # Never register any tool call — event never fires

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={})

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
        # Make wait_for immediately time out so the test runs in <1s
        patch("src.permission_service.asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        mock_pr.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})
        coord.session_manager = MagicMock()
        coord.session_manager.get_session_info = AsyncMock(
            return_value=MagicMock(current_permission_mode="default")
        )

        cb = svc.create_permission_callback(session_id)

        start = time.monotonic()
        result = await cb("Bash", {"command": "rm -rf /"}, None)
        elapsed = time.monotonic() - start

        assert result == {"behavior": "deny"}, "Should auto-deny on timeout"
        assert elapsed < 2.0, "Should complete within 2 seconds"


# ---------------------------------------------------------------------------
# Test 5: event.set() called by create_tool_call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_858_event_set_on_create_tool_call():
    """create_tool_call() must signal the per-session event."""
    import tempfile

    from src.session_coordinator import SessionCoordinator

    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(data_dir=Path(tmp))
        session_id = "test-session-event"

        event = coord.get_tool_call_event(session_id)
        assert not event.is_set()

        coord.create_tool_call(
            session_id=session_id,
            tool_use_id="tu-001",
            name="Read",
            input_params={"file_path": "/x.txt"},
            requires_permission=False,
        )

        assert event.is_set(), "Event must be set after create_tool_call()"


# ---------------------------------------------------------------------------
# Test 6: event cleaned up on terminate_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_858_event_cleaned_up_on_terminate():
    """After terminate_session(), session_id must be absent from _tool_call_events."""
    import tempfile

    from src.session_coordinator import SessionCoordinator

    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(data_dir=Path(tmp))
        session_id = "test-session-cleanup"

        coord.get_tool_call_event(session_id)
        assert session_id in coord._tool_call_events

        with (
            patch.object(coord.queue_processor, "stop"),
            patch.object(
                coord.session_manager,
                "update_processing_state",
                new_callable=AsyncMock,
            ),
            patch.object(coord, "_mark_tools_orphaned"),
            patch.object(coord, "mark_session_tools_interrupted"),
            patch.object(
                coord.session_manager,
                "terminate_session",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("src.session_coordinator.cleanup_session_tmp"),
            patch.object(coord, "_notify_state_change", new_callable=AsyncMock),
        ):
            await coord.terminate_session(session_id)

        assert session_id not in coord._tool_call_events, (
            "Event must be removed from _tool_call_events after terminate_session()"
        )
