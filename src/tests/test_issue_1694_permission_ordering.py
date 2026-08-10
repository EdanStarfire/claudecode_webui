"""
Regression tests for issue #1694.

Permission prompt renders before its assistant response in realtime but after when
loaded from storage (intermittent ordering race).

Covers:
- ToolCall.message_id serialization round-trip.
- SessionCoordinator.create_tool_call() threading message_id through.
- Emission order in web_server._create_message_callback(): the assistant message
  envelope must be queued (and the message-emitted barrier marked) before
  _emit_tool_call_updates() creates the tool_call PENDING event.
- The message_id-keyed barrier (SessionCoordinator.mark_assistant_message_emitted /
  is_assistant_message_emitted / get_message_emitted_event) semantics.
- permission_service.py's barrier wait: resolves promptly once marked, fails open
  (never denies) on timeout, and is a complete no-op when tool_call.message_id is
  unset — preserving today's behavior for every existing call path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.messages import ToolCall, ToolDisplayInfo, ToolState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call(
    session_id: str, name: str, input_params: dict, message_id: str | None = None
) -> ToolCall:
    return ToolCall(
        tool_use_id=f"tu_{name}",
        session_id=session_id,
        name=name,
        input=input_params,
        status=ToolState.PENDING,
        created_at=time.time(),
        requires_permission=True,
        parent_tool_use_id=None,
        message_id=message_id,
        display=ToolDisplayInfo(
            state=ToolState.PENDING,
            visible=True,
            collapsed=False,
            style="default",
        ),
    )


def _make_coordinator(session_id: str) -> MagicMock:
    """Build a minimal mock SessionCoordinator backed by real asyncio.Events,
    mirroring both the #858 tool-call-existence barrier and the #1694
    message-emitted barrier."""
    coord = MagicMock()
    tool_call_event = asyncio.Event()
    message_emitted_event = asyncio.Event()
    tool_calls: dict[str, ToolCall] = {}
    emitted_message_ids: set[str] = set()

    coord.get_tool_call_event.side_effect = lambda sid: tool_call_event
    coord.find_tool_call_by_signature.side_effect = lambda sid, name, params: tool_calls.get(name)
    coord.is_uploaded_file.side_effect = lambda sid, path: False
    coord.get_message_emitted_event.side_effect = lambda sid: message_emitted_event
    coord.is_assistant_message_emitted.side_effect = lambda sid, mid: mid in emitted_message_ids

    def _mark(sid: str, mid: str) -> None:
        emitted_message_ids.add(mid)
        message_emitted_event.set()

    coord.mark_assistant_message_emitted.side_effect = _mark

    coord._tool_calls = tool_calls
    coord._event = tool_call_event
    coord._message_emitted_event = message_emitted_event
    coord._emitted_message_ids = emitted_message_ids
    return coord


# ---------------------------------------------------------------------------
# ToolCall.message_id serialization
# ---------------------------------------------------------------------------

def test_tool_call_message_id_round_trip():
    """to_dict()/from_dict() must preserve message_id."""
    tc = _make_tool_call("sess-a", "Edit", {"file_path": "/x.py"}, message_id="msg_123")
    data = tc.to_dict()
    assert data["message_id"] == "msg_123"

    restored = ToolCall.from_dict(data)
    assert restored.message_id == "msg_123"


def test_tool_call_message_id_omitted_when_none():
    """Backward compat: to_dict() must not emit a message_id key when unset."""
    tc = _make_tool_call("sess-a", "Edit", {"file_path": "/x.py"})
    data = tc.to_dict()
    assert "message_id" not in data

    restored = ToolCall.from_dict(data)
    assert restored.message_id is None


def test_tool_call_with_status_update_preserves_message_id():
    """with_status_update() round-trips through to_dict/from_dict, so message_id
    must survive an unrelated status transition."""
    tc = _make_tool_call("sess-a", "Edit", {"file_path": "/x.py"}, message_id="msg_123")
    updated = tc.with_status_update(status=ToolState.RUNNING)
    assert updated.message_id == "msg_123"


# ---------------------------------------------------------------------------
# SessionCoordinator: create_tool_call() threads message_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_tool_call_stores_message_id(tmp_path):
    from src.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-create"

    tool_call = coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Read",
        input_params={"file_path": "/x.txt"},
        requires_permission=False,
        message_id="msg_create_001",
    )

    assert tool_call.message_id == "msg_create_001"
    stored = coord.get_tool_call_by_id(session_id, "tu-001")
    assert stored.message_id == "msg_create_001"


@pytest.mark.asyncio
async def test_create_tool_call_message_id_defaults_to_none(tmp_path):
    """Existing call sites that don't pass message_id must be unaffected."""
    from src.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-default"

    tool_call = coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-002",
        name="Read",
        input_params={"file_path": "/x.txt"},
        requires_permission=False,
    )

    assert tool_call.message_id is None


# ---------------------------------------------------------------------------
# SessionCoordinator: message-emitted barrier semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_emitted_barrier_mark_and_check(tmp_path):
    from src.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-barrier"

    assert not coord.is_assistant_message_emitted(session_id, "msg_1")

    event = coord.get_message_emitted_event(session_id)
    assert not event.is_set()

    coord.mark_assistant_message_emitted(session_id, "msg_1")

    assert event.is_set(), "Event must be set after mark_assistant_message_emitted()"
    assert coord.is_assistant_message_emitted(session_id, "msg_1")
    assert not coord.is_assistant_message_emitted(session_id, "msg_2"), (
        "A different message_id in the same session must not be considered emitted"
    )


@pytest.mark.asyncio
async def test_message_emitted_barrier_cleaned_up_on_terminate(tmp_path):
    from src.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-cleanup"

    coord.get_message_emitted_event(session_id)
    coord.mark_assistant_message_emitted(session_id, "msg_1")
    assert session_id in coord._message_emitted_events
    assert session_id in coord._emitted_message_ids

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

    assert session_id not in coord._message_emitted_events
    assert session_id not in coord._emitted_message_ids


# ---------------------------------------------------------------------------
# web_server._create_message_callback: emission order
# ---------------------------------------------------------------------------

def _make_webui(tmp_path):
    from src.web_server import ClaudeWebUI

    webui = ClaudeWebUI(data_dir=tmp_path)
    processor = MagicMock()
    processor.prepare_for_websocket.return_value = {"type": "assistant", "content": "hi"}
    webui._message_processor = processor
    return webui


@pytest.mark.asyncio
async def test_emission_order_envelope_before_tool_call_pending(tmp_path):
    """The assistant message envelope must be appended to the session queue, and the
    message-emitted barrier marked, BEFORE the tool_call PENDING event is emitted."""
    session_id = "sess-1694-order"

    webui = _make_webui(tmp_path)
    webui.session_queues[session_id] = []

    call_order = []
    coordinator = MagicMock()

    def _mark(sid, mid):
        call_order.append(("mark_assistant_message_emitted", mid))

    coordinator.mark_assistant_message_emitted.side_effect = _mark

    def _create_tool_call(**kwargs):
        call_order.append(("create_tool_call", kwargs["tool_use_id"]))
        return ToolCall(
            tool_use_id=kwargs["tool_use_id"],
            session_id=kwargs["session_id"],
            name=kwargs["name"],
            input=kwargs["input_params"],
            status=ToolState.PENDING,
            created_at=time.time(),
            requires_permission=kwargs.get("requires_permission", False),
            parent_tool_use_id=kwargs.get("parent_tool_use_id"),
            message_id=kwargs.get("message_id"),
            display=ToolDisplayInfo(
                state=ToolState.PENDING, visible=True, collapsed=False, style="default"
            ),
        )

    coordinator.create_tool_call.side_effect = _create_tool_call
    webui.coordinator = coordinator

    parsed_message = MagicMock()
    parsed_message.type = MagicMock(value="assistant")
    parsed_message.metadata = {
        "message_id": "msg_abc",
        "tool_uses": [{"id": "tu1", "name": "Bash", "input": {"command": "ls"}}],
    }

    callback = webui._create_message_callback(session_id)
    await callback(session_id, parsed_message)

    # Ordering: mark_assistant_message_emitted() happens before create_tool_call()
    assert call_order == [
        ("mark_assistant_message_emitted", "msg_abc"),
        ("create_tool_call", "tu1"),
    ]

    # Ordering: the envelope entry is queued ahead of the tool_call PENDING entry
    queue = webui.session_queues[session_id]
    assert len(queue) == 2
    assert queue[0]["data"].get("type") != "tool_call"
    assert queue[1]["data"]["type"] == "tool_call"


# ---------------------------------------------------------------------------
# permission_service.py: barrier wait behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_permission_barrier_resolves_on_mark():
    """Barrier wait resolves promptly once mark_assistant_message_emitted() fires,
    and the awaiting_permission update proceeds."""
    session_id = "sess-1694-resolve"
    tool_use_id = "tu_1694_a"
    message_id = "msg_1694_a"

    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Edit", {"file_path": "/x.py"}, message_id=message_id)
    tc.tool_use_id = tool_use_id
    coord._tool_calls["Edit"] = tc
    coord.get_tool_call_by_id = MagicMock(side_effect=lambda sid, tuid: tc if tuid == tool_use_id else None)
    coord.update_tool_call_permission_request = MagicMock(return_value=None)
    coord.session_manager = MagicMock()
    coord.session_manager.get_session_info = AsyncMock(
        return_value=MagicMock(current_permission_mode="default")
    )
    coord.session_manager.pause_session = AsyncMock()

    async def mark_after_delay():
        await asyncio.sleep(0.05)
        coord.mark_assistant_message_emitted(session_id, message_id)

    asyncio.create_task(mark_after_delay())

    ctx = MagicMock()
    ctx.tool_use_id = tool_use_id
    ctx.agent_id = None
    ctx.suggestions = []

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={session_id: []})

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
    ):
        mock_pr.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})

        cb = svc.create_permission_callback(session_id)
        start = asyncio.get_event_loop().time()
        task = asyncio.create_task(cb("Edit", {"file_path": "/x.py"}, ctx))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        elapsed = asyncio.get_event_loop().time() - start

    coord.update_tool_call_permission_request.assert_called()
    assert elapsed < 1.0, "Should resolve well under 1 second once marked"


@pytest.mark.asyncio
async def test_permission_barrier_fails_open_on_timeout(caplog):
    """When the barrier never fires, the callback logs a warning and proceeds
    anyway (fail-open) — it must NOT auto-deny, since this only affects display
    ordering, not tool execution correctness."""
    session_id = "sess-1694-timeout"
    tool_use_id = "tu_1694_b"
    message_id = "msg_1694_b"

    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Write", {"file_path": "/y.py"}, message_id=message_id)
    tc.tool_use_id = tool_use_id
    coord._tool_calls["Write"] = tc
    coord.get_tool_call_by_id = MagicMock(side_effect=lambda sid, tuid: tc if tuid == tool_use_id else None)
    coord.update_tool_call_permission_request = MagicMock(return_value=None)
    coord.session_manager = MagicMock()
    coord.session_manager.get_session_info = AsyncMock(
        return_value=MagicMock(current_permission_mode="default")
    )
    coord.session_manager.pause_session = AsyncMock()
    # Barrier never marked — is_assistant_message_emitted() stays False forever.

    ctx = MagicMock()
    ctx.tool_use_id = tool_use_id
    ctx.agent_id = None
    ctx.suggestions = []

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={session_id: []})

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
        # Make wait_for immediately time out so the test runs in well under 2s
        patch("src.permission_service.asyncio.wait_for", side_effect=asyncio.TimeoutError),
        # configure_logging() (run by other test modules earlier in the same session)
        # unconditionally sets propagate=False on every category logger it manages,
        # including 'sdk_debug' — caplog's handler is only attached to the root
        # logger, so propagation must be forced on for it to see these records.
        patch.object(logging.getLogger("sdk_debug"), "propagate", True),
        caplog.at_level("WARNING", logger="sdk_debug"),
    ):
        mock_pr.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})

        cb = svc.create_permission_callback(session_id)
        start = time.monotonic()
        task = asyncio.create_task(cb("Write", {"file_path": "/y.py"}, ctx))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        elapsed = time.monotonic() - start

    assert elapsed < 1.0, "Fail-open must not block on the full ~2s deadline"
    coord.update_tool_call_permission_request.assert_called(), (
        "Must proceed past the barrier (not auto-deny) once it fails open"
    )
    assert any("Proceeding anyway" in r.message for r in caplog.records), (
        "Must log a warning when failing open"
    )


@pytest.mark.asyncio
async def test_permission_barrier_skipped_when_message_id_absent():
    """Zero behavior change: when the ToolCall has no message_id (every existing
    test helper and call path today), the barrier must not be touched at all."""
    session_id = "sess-1694-noop"
    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Read", {"file_path": "/foo.txt"})  # message_id=None
    coord._tool_calls["Read"] = tc
    coord.update_tool_call_permission_request = MagicMock(return_value=None)
    coord.session_manager = MagicMock()
    coord.session_manager.get_session_info = AsyncMock(
        return_value=MagicMock(current_permission_mode="default")
    )
    coord.session_manager.pause_session = AsyncMock()

    from src.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={session_id: []})

    with (
        patch("src.permission_service.PermissionRequestMessage") as mock_pr,
        patch("src.permission_service.StoredMessage") as mock_sm,
        patch("src.permission_service.PermissionInfo"),
    ):
        mock_pr.return_value = MagicMock()
        mock_sm.from_permission_request.return_value = MagicMock(to_dict=lambda: {})

        cb = svc.create_permission_callback(session_id)
        task = asyncio.create_task(cb("Read", {"file_path": "/foo.txt"}, None))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    coord.is_assistant_message_emitted.assert_not_called()
    coord.get_message_emitted_event.assert_not_called()
    coord.update_tool_call_permission_request.assert_called()
