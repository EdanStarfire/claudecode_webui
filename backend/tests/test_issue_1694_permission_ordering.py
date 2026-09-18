"""
Regression tests for issue #1694.

Permission prompt renders before its assistant response in realtime but after when
loaded from storage (intermittent ordering race).

Covers:
- ToolCall.turn_id serialization round-trip (renamed from message_id by issue #1958).
- SessionCoordinator.create_tool_call() threading turn_id through.
- Emission order in web_server._create_message_callback(): the assistant message
  envelope must be queued (and the message-emitted barrier marked) before
  _emit_tool_call_updates() creates the tool_call PENDING event.
- The turn_id-keyed barrier (SessionCoordinator.mark_assistant_message_emitted /
  is_assistant_message_emitted / get_message_emitted_event) semantics.
- permission_service.py's barrier wait: resolves promptly once marked, fails open
  (never denies) on timeout, and is a complete no-op when tool_call.turn_id is
  unset — preserving today's behavior for every existing call path.

Issue #1958 renamed the ambiguous shared `message_id` name (used for both per-turn and
per-record identity) throughout this barrier: ToolCall.message_id -> ToolCall.turn_id,
and SessionCoordinator._emitted_message_ids -> _emitted_turn_ids. The live poll payload's
`message_id` key is unaffected — it was always meant to be per-record identity and keeps
its name; see ParsedMessage.record_id in backend/message_parser.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.messages import ToolCall, ToolDisplayInfo, ToolState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call(
    session_id: str, name: str, input_params: dict, turn_id: str | None = None
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
        turn_id=turn_id,
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
    emitted_turn_ids: set[str] = set()

    coord.get_tool_call_event.side_effect = lambda sid: tool_call_event
    coord.find_tool_call_by_signature.side_effect = lambda sid, name, params: tool_calls.get(name)
    coord.is_uploaded_file.side_effect = lambda sid, path: False
    coord.get_message_emitted_event.side_effect = lambda sid: message_emitted_event
    coord.is_assistant_message_emitted.side_effect = lambda sid, tid: tid in emitted_turn_ids

    def _mark(sid: str, tid: str) -> None:
        emitted_turn_ids.add(tid)
        message_emitted_event.set()

    coord.mark_assistant_message_emitted.side_effect = _mark

    coord._tool_calls = tool_calls
    coord._event = tool_call_event
    coord._message_emitted_event = message_emitted_event
    coord._emitted_turn_ids = emitted_turn_ids
    return coord


# ---------------------------------------------------------------------------
# ToolCall.turn_id serialization
# ---------------------------------------------------------------------------

def test_tool_call_turn_id_round_trip():
    """to_dict()/from_dict() must preserve turn_id."""
    tc = _make_tool_call("sess-a", "Edit", {"file_path": "/x.py"}, turn_id="msg_123")
    data = tc.to_dict()
    assert data["turn_id"] == "msg_123"

    restored = ToolCall.from_dict(data)
    assert restored.turn_id == "msg_123"


def test_tool_call_turn_id_omitted_when_none():
    """Backward compat: to_dict() must not emit a turn_id key when unset."""
    tc = _make_tool_call("sess-a", "Edit", {"file_path": "/x.py"})
    data = tc.to_dict()
    assert "turn_id" not in data

    restored = ToolCall.from_dict(data)
    assert restored.turn_id is None


def test_tool_call_with_status_update_preserves_turn_id():
    """with_status_update() round-trips through to_dict/from_dict, so turn_id
    must survive an unrelated status transition."""
    tc = _make_tool_call("sess-a", "Edit", {"file_path": "/x.py"}, turn_id="msg_123")
    updated = tc.with_status_update(status=ToolState.RUNNING)
    assert updated.turn_id == "msg_123"


def test_tool_call_from_dict_legacy_message_id_fallback():
    """Backward compat (QA-flagged gap): pre-#1958 ToolCallUpdate records persisted to
    messages.jsonl carry the turn id under the old ambiguous "message_id" key, not
    "turn_id". from_dict() must still resolve turn_id when replaying that legacy shape —
    mirrors the equivalent metadata["turn_id"]/metadata["message_id"] fallback already in
    place for assistant messages in message_parser.py."""
    legacy_data = {
        "tool_use_id": "tu_legacy",
        "session_id": "sess-a",
        "name": "Edit",
        "input": {"file_path": "/x.py"},
        "status": "pending",
        "created_at": 0.0,
        "requires_permission": True,
        "message_id": "msg_legacy_turn",
    }
    restored = ToolCall.from_dict(legacy_data)
    assert restored.turn_id == "msg_legacy_turn"


def test_tool_call_from_dict_prefers_turn_id_over_legacy_message_id():
    """If a record somehow carries both keys, the new turn_id key must win."""
    data = {
        "tool_use_id": "tu_both",
        "session_id": "sess-a",
        "name": "Edit",
        "input": {"file_path": "/x.py"},
        "status": "pending",
        "created_at": 0.0,
        "requires_permission": True,
        "turn_id": "msg_new_turn",
        "message_id": "msg_legacy_turn",
    }
    restored = ToolCall.from_dict(data)
    assert restored.turn_id == "msg_new_turn"


# ---------------------------------------------------------------------------
# SessionCoordinator: create_tool_call() threads turn_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_tool_call_stores_turn_id(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-create"

    tool_call = coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Read",
        input_params={"file_path": "/x.txt"},
        requires_permission=False,
        turn_id="msg_create_001",
    )

    assert tool_call.turn_id == "msg_create_001"
    stored = coord.get_tool_call_by_id(session_id, "tu-001")
    assert stored.turn_id == "msg_create_001"


@pytest.mark.asyncio
async def test_create_tool_call_turn_id_defaults_to_none(tmp_path):
    """Existing call sites that don't pass turn_id must be unaffected."""
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-default"

    tool_call = coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-002",
        name="Read",
        input_params={"file_path": "/x.txt"},
        requires_permission=False,
    )

    assert tool_call.turn_id is None


# ---------------------------------------------------------------------------
# SessionCoordinator: message-emitted barrier semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_emitted_barrier_mark_and_check(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-barrier"

    assert not coord.is_assistant_message_emitted(session_id, "msg_1")

    event = coord.get_message_emitted_event(session_id)
    assert not event.is_set()

    coord.mark_assistant_message_emitted(session_id, "msg_1")

    assert event.is_set(), "Event must be set after mark_assistant_message_emitted()"
    assert coord.is_assistant_message_emitted(session_id, "msg_1")
    assert not coord.is_assistant_message_emitted(session_id, "msg_2"), (
        "A different turn_id in the same session must not be considered emitted"
    )


@pytest.mark.asyncio
async def test_message_emitted_barrier_cleaned_up_on_terminate(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "test-session-1694-cleanup"

    coord.get_message_emitted_event(session_id)
    coord.mark_assistant_message_emitted(session_id, "msg_1")
    assert session_id in coord._message_emitted_events
    assert session_id in coord._emitted_turn_ids

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
        patch("backend.session_coordinator.cleanup_session_tmp"),
        patch.object(coord, "_notify_state_change", new_callable=AsyncMock),
    ):
        await coord.terminate_session(session_id)

    assert session_id not in coord._message_emitted_events
    assert session_id not in coord._emitted_turn_ids


# ---------------------------------------------------------------------------
# web_server._create_message_callback: emission order
# ---------------------------------------------------------------------------

def _make_webui(tmp_path):
    from backend.web_server import BackendApp

    webui = BackendApp(data_dir=tmp_path)
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
            turn_id=kwargs.get("turn_id"),
            display=ToolDisplayInfo(
                state=ToolState.PENDING, visible=True, collapsed=False, style="default"
            ),
        )

    coordinator.create_tool_call.side_effect = _create_tool_call
    webui.coordinator = coordinator

    parsed_message = MagicMock()
    parsed_message.type = MagicMock(value="assistant")
    parsed_message.record_id = "frame-uuid-abc"
    parsed_message.turn_id = "msg_abc"
    parsed_message.metadata = {
        "turn_id": "msg_abc",
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


@pytest.mark.asyncio
async def test_issue_1958_callback_separates_record_id_from_turn_id_barrier(tmp_path):
    """Issue #1957/#1958: for a live AssistantMessage, the live poll payload
    (websocket_data['message_id']) must carry parsed_message.record_id (the PER-RECORD
    identity), while the #1694 barrier must be marked with parsed_message.turn_id (the
    PER-TURN Anthropic id) — even though the two values differ on the same message. This
    is now a direct field read on ParsedMessage (populated centrally by
    MessageProcessor.process_message()), not dict-digging through raw_data/metadata."""
    session_id = "sess-1958-record-vs-turn"

    webui = _make_webui(tmp_path)
    webui.session_queues[session_id] = []

    coordinator = MagicMock()
    marked = []
    coordinator.mark_assistant_message_emitted.side_effect = lambda sid, tid: marked.append(tid)
    coordinator.create_tool_call.return_value = None
    webui.coordinator = coordinator

    # A already-parsed ParsedMessage-shaped object, mirroring what
    # SessionCoordinator._create_message_callback() hands to this callback in
    # production: record_id (per-record) and turn_id (per-turn) as distinct fields.
    parsed_message = MagicMock()
    parsed_message.type = MagicMock(value="assistant")
    parsed_message.record_id = "frame-uuid-per-message"
    parsed_message.turn_id = "msg_anthropic_turn_shared"
    parsed_message.metadata = {"turn_id": "msg_anthropic_turn_shared"}

    callback = webui._create_message_callback(session_id)
    await callback(session_id, parsed_message)

    queue = webui.session_queues[session_id]
    assert len(queue) == 1
    # Live payload carries the PER-RECORD id — this is what #1955's frontend single-rule
    # dedup keys on, and it must differ per frame even across one shared Anthropic turn.
    assert queue[0]["data"]["message_id"] == "frame-uuid-per-message"

    # The barrier is marked with the TURN-level id — NOT the per-record id.
    assert marked == ["msg_anthropic_turn_shared"]


@pytest.mark.asyncio
async def test_issue_1957_end_to_end_two_frames_one_turn_through_real_pipeline(tmp_path):
    """Issue #1957 integration test: chains the REAL ClaudeSDK._store_sdk_message() output
    directly into the REAL BackendApp._create_message_callback() callback (no hand-built
    stand-in dicts), for two frames sharing one Anthropic turn — the exact #1765
    background-Task-launch shape from the live-testing repro. Confirms the live poll queue
    ends up with two DISTINCT message_id values (frontend dedup survives), while both
    frames' storage_manager.append_message() calls also each got that same distinct id
    (live and stored stay aligned per frame).

    Issue #1958 moved the per-record UUID stamp out of _store_sdk_message() and into
    _process_sdk_message() (see claude_sdk.py), so it now runs unconditionally regardless
    of storage_manager. This test calls _store_sdk_message() directly (bypassing
    _process_sdk_message()), so it stamps each frame's message_id itself first, exactly
    mirroring what _process_sdk_message() would have done immediately beforehand in
    production.
    """
    import uuid

    from backend.claude_sdk import ClaudeSDK
    from backend.session_config import SessionConfig

    session_id = "sess-1957-e2e"

    sdk = ClaudeSDK(
        session_id=session_id,
        working_directory=str(tmp_path),
        config=SessionConfig(system_prompt="test"),
    )
    storage_manager = MagicMock()
    storage_manager.append_message = AsyncMock()
    sdk.storage_manager = storage_manager

    webui = _make_webui(tmp_path)
    webui.session_queues[session_id] = []
    coordinator = MagicMock()
    webui.coordinator = coordinator
    callback = webui._create_message_callback(session_id)
    sdk.message_callback = callback

    shared_turn_metadata = {"turn_id": "msg_anthropic_turn_shared_e2e", "tool_uses": []}
    frame_1 = {
        "type": "assistant", "content": "Launching agent A", "timestamp": 1.0,
        "session_id": session_id, "metadata": dict(shared_turn_metadata),
        "message_id": str(uuid.uuid4()),
    }
    frame_2 = {
        "type": "assistant", "content": "Launching agent B", "timestamp": 2.0,
        "session_id": session_id, "metadata": dict(shared_turn_metadata),
        "message_id": str(uuid.uuid4()),
    }

    # Real _message_processor (not mocked) so process_message()/prepare_for_websocket()
    # actually run and reflect the top-level message_id into parsed_message.record_id
    # correctly.
    from backend.message_parser import MessageParser, MessageProcessor
    webui._message_processor = MessageProcessor(MessageParser())

    await sdk._store_sdk_message(frame_1)
    await callback(session_id, frame_1)
    await sdk._store_sdk_message(frame_2)
    await callback(session_id, frame_2)

    queue = webui.session_queues[session_id]
    assert len(queue) == 2
    live_ids = [entry["data"]["message_id"] for entry in queue]
    assert live_ids[0] != live_ids[1], (
        "Two frames of one Anthropic turn must get distinct live message_ids, or "
        "the frontend's single-rule dedup silently drops the second frame (#1957)."
    )

    stored_ids = [c.args[0]["message_id"] for c in storage_manager.append_message.call_args_list]
    assert stored_ids == live_ids, "Live-delivered and persisted identities must match per frame."


@pytest.mark.asyncio
async def test_issue_1957_full_production_wiring_two_frames_distinct_live_ids(tmp_path):
    """Issue #1957 follow-up (found via live testing: the fix above was dead code in
    production). On the REAL live path, ClaudeSDK's message_callback is
    SessionCoordinator._create_message_callback, NOT web_server.py's callback directly —
    the test above calling `callback(session_id, frame_1)` skips this conversion step
    entirely, which is exactly why it passed while production stayed broken.

    SessionCoordinator._create_message_callback() converts the raw dict into a
    ParsedMessage via MessageProcessor.process_message() BEFORE fanning out to registered
    subscribers (web_server.py's callback, added via add_message_callback() — mirroring
    exactly how backend/web_server.py wires itself up at session start in production).
    `message_data` inside web_server.py's callback is therefore always a ParsedMessage
    object, never a dict. Issue #1958 replaced the old dict-digging propagation chain
    entirely with a direct `parsed_message.record_id` field read, populated centrally by
    MessageProcessor.process_message() from the ParsedMessage's own raw_data — so the
    per-frame identity survives the dict->ParsedMessage conversion by construction, not by
    a chain of fallback branches.

    This test wires the two REAL callback factories together, exactly as production does,
    and asserts what a live burst of tool calls in one turn actually needs: distinct
    per-frame message_ids reaching the poll queue, not the shared turn-level id."""
    from backend.message_parser import MessageParser, MessageProcessor
    from backend.session_coordinator import SessionCoordinator

    session_id = "sess-1957-full-wiring"
    coord = SessionCoordinator(data_dir=tmp_path)

    webui = _make_webui(tmp_path)
    webui._message_processor = MessageProcessor(MessageParser())
    webui.session_queues[session_id] = []
    webui.coordinator = MagicMock()

    # Register web_server.py's real callback as a SessionCoordinator subscriber — exactly
    # how ClaudeSDK's message_callback (== coord._create_message_callback) fans out to it
    # in production.
    webui_callback = webui._create_message_callback(session_id)
    coord.add_message_callback(session_id, webui_callback)
    sdk_callback = coord._create_message_callback(session_id)

    # Two frames sharing one Anthropic turn (metadata.turn_id), each with its own
    # per-frame message_id already stamped — mirrors ClaudeSDK._process_sdk_message()'s
    # stamp output exactly.
    shared_turn_metadata = {"turn_id": "msg_anthropic_turn_shared_full"}
    frame_1 = {
        "type": "assistant", "content": "Launching agent A", "timestamp": 1.0,
        "session_id": session_id, "message_id": "frame-uuid-A", "metadata": dict(shared_turn_metadata),
    }
    frame_2 = {
        "type": "assistant", "content": "Launching agent B", "timestamp": 2.0,
        "session_id": session_id, "message_id": "frame-uuid-B", "metadata": dict(shared_turn_metadata),
    }

    await sdk_callback(frame_1)
    await sdk_callback(frame_2)

    queue = webui.session_queues[session_id]
    assert len(queue) == 2, f"Expected both frames to reach the poll queue, got {len(queue)}"
    live_ids = [entry["data"]["message_id"] for entry in queue]
    assert live_ids == ["frame-uuid-A", "frame-uuid-B"], (
        f"Per-frame identity must survive the dict->ParsedMessage conversion "
        f"SessionCoordinator._create_message_callback() performs; got {live_ids}"
    )


# ---------------------------------------------------------------------------
# permission_service.py: barrier wait behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_permission_barrier_resolves_on_mark():
    """Barrier wait resolves promptly once mark_assistant_message_emitted() fires,
    and the awaiting_permission update proceeds."""
    session_id = "sess-1694-resolve"
    tool_use_id = "tu_1694_a"
    turn_id = "msg_1694_a"

    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Edit", {"file_path": "/x.py"}, turn_id=turn_id)
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
        coord.mark_assistant_message_emitted(session_id, turn_id)

    asyncio.create_task(mark_after_delay())

    ctx = MagicMock()
    ctx.tool_use_id = tool_use_id
    ctx.agent_id = None
    ctx.suggestions = []

    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={session_id: []})

    with (
        patch("backend.permission_service.PermissionRequestMessage") as mock_pr,
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
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
    turn_id = "msg_1694_b"

    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Write", {"file_path": "/y.py"}, turn_id=turn_id)
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

    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={session_id: []})

    with (
        patch("backend.permission_service.PermissionRequestMessage") as mock_pr,
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
        # Make wait_for immediately time out so the test runs in well under 2s
        patch("backend.permission_service.asyncio.wait_for", side_effect=asyncio.TimeoutError),
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
async def test_permission_barrier_skipped_when_turn_id_absent():
    """Zero behavior change: when the ToolCall has no turn_id (every existing
    test helper and call path today), the barrier must not be touched at all."""
    session_id = "sess-1694-noop"
    coord = _make_coordinator(session_id)
    tc = _make_tool_call(session_id, "Read", {"file_path": "/foo.txt"})  # turn_id=None
    coord._tool_calls["Read"] = tc
    coord.update_tool_call_permission_request = MagicMock(return_value=None)
    coord.session_manager = MagicMock()
    coord.session_manager.get_session_info = AsyncMock(
        return_value=MagicMock(current_permission_mode="default")
    )
    coord.session_manager.pause_session = AsyncMock()

    from backend.permission_service import PermissionService

    svc = PermissionService(coordinator=coord, session_queues={session_id: []})

    with (
        patch("backend.permission_service.PermissionRequestMessage") as mock_pr,
        patch("backend.permission_service.StoredMessage") as mock_sm,
        patch("backend.permission_service.PermissionInfo"),
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


# ---------------------------------------------------------------------------
# Issue #1958: new identity-propagation coverage — production wiring shape,
# fail-loud on missing record_id, and unconditional stamp timing.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1958_multi_record_turn_full_production_wiring(tmp_path):
    """Issue #1958's explicit, named requirement: drive a multi-record assistant turn
    (three separate frames sharing one Anthropic turn) through the REAL production
    wiring shape — ClaudeSDK._process_sdk_message() -> its message_callback, which in
    production IS SessionCoordinator._create_message_callback() -> fans out to
    BackendApp._create_message_callback() as a registered subscriber, exactly as
    session_coordinator.py wires ClaudeSDK(message_callback=self._create_message_callback(...))
    at session start.

    Asserts all three of the issue's named guarantees: every record gets a distinct
    record_id, all three share one turn_id, and the live-delivered ids match what
    gets persisted to storage.
    """
    from backend.claude_sdk import ClaudeSDK
    from backend.message_parser import MessageParser, MessageProcessor
    from backend.session_config import SessionConfig
    from backend.session_coordinator import SessionCoordinator

    session_id = "sess-1958-multi-record"
    coord = SessionCoordinator(data_dir=tmp_path)

    webui = _make_webui(tmp_path)
    webui._message_processor = MessageProcessor(MessageParser())
    webui.session_queues[session_id] = []
    webui.coordinator = MagicMock()

    # Register web_server.py's real callback as a SessionCoordinator subscriber, then use
    # SessionCoordinator's own real callback as ClaudeSDK's message_callback — the exact
    # two-hop chain production wires at session start.
    webui_callback = webui._create_message_callback(session_id)
    coord.add_message_callback(session_id, webui_callback)
    sdk_callback = coord._create_message_callback(session_id)

    sdk = ClaudeSDK(
        session_id=session_id,
        working_directory=str(tmp_path),
        config=SessionConfig(system_prompt="test"),
    )
    storage_manager = MagicMock()
    storage_manager.append_message = AsyncMock()
    sdk.storage_manager = storage_manager
    sdk.message_callback = sdk_callback

    shared_turn_metadata = {"turn_id": "msg_anthropic_turn_multi"}
    frames = [
        {
            "type": "assistant", "content": "Thinking about it", "timestamp": 1.0,
            "session_id": session_id, "metadata": dict(shared_turn_metadata),
        },
        {
            "type": "assistant", "content": "Launching tool A", "timestamp": 2.0,
            "session_id": session_id, "metadata": dict(shared_turn_metadata),
        },
        {
            "type": "assistant", "content": "Launching tool B", "timestamp": 3.0,
            "session_id": session_id, "metadata": dict(shared_turn_metadata),
        },
    ]

    for frame in frames:
        await sdk._process_sdk_message(frame)

    queue = webui.session_queues[session_id]
    assert len(queue) == 3, f"Expected all three records to reach the poll queue, got {len(queue)}"

    live_record_ids = [entry["data"]["message_id"] for entry in queue]
    assert len(set(live_record_ids)) == 3, (
        f"Every record must get a distinct record_id, got {live_record_ids}"
    )

    stored_ids = [c.args[0]["message_id"] for c in storage_manager.append_message.call_args_list]
    assert stored_ids == live_record_ids, (
        "Live-delivered and persisted record_ids must match per record."
    )


@pytest.mark.asyncio
async def test_issue_1958_fail_loud_when_record_id_missing(tmp_path, caplog):
    """Issue #1958 acceptance criteria: when a live message reaches web_server.py's
    callback with no record_id, this must produce a visible logged error — NOT a silent
    fallback to the turn-level id. Silent turn-level substitution on a record_id miss is
    the exact anti-pattern behind #1955/#1957/#1957-followup; the rename replaces the old
    4-branch dict-digging chain specifically to make this failure mode structurally
    impossible to reintroduce."""
    session_id = "sess-1958-fail-loud"
    webui = _make_webui(tmp_path)
    webui.session_queues[session_id] = []
    webui.coordinator = MagicMock()

    parsed_message = MagicMock()
    parsed_message.type = MagicMock(value="assistant")
    parsed_message.record_id = None
    parsed_message.turn_id = "msg_turn_only"
    parsed_message.metadata = {"turn_id": "msg_turn_only"}

    callback = webui._create_message_callback(session_id)
    with caplog.at_level("ERROR", logger="backend.web_server"):
        await callback(session_id, parsed_message)

    queue = webui.session_queues[session_id]
    assert len(queue) == 1
    assert "message_id" not in queue[0]["data"], (
        "No turn-level substitution allowed into websocket_data['message_id'] when "
        "record_id is absent — this is the exact anti-pattern #1958 eliminates."
    )
    assert any("record_id" in r.message for r in caplog.records), (
        "A missing record_id on the live path must produce a visible logged error"
    )


@pytest.mark.asyncio
async def test_issue_1958_stamp_timing_unconditional_without_storage_manager(tmp_path):
    """Issue #1958 stamp-timing fix: the per-record UUID stamp must happen
    unconditionally in _process_sdk_message(), immediately after _convert_sdk_message()
    returns — NOT gated behind `if self.storage_manager` inside _store_sdk_message() as
    it was before. A ClaudeSDK instance with no storage_manager configured must still
    stamp a record_id (converted_message['message_id']) before the message callback
    fires, closing the gap where a live message could otherwise reach the callback with
    no record_id at all and silently trip the fail-loud branch this issue introduces."""
    from backend.claude_sdk import ClaudeSDK
    from backend.session_config import SessionConfig

    session_id = "sess-1958-stamp-timing"
    sdk = ClaudeSDK(
        session_id=session_id,
        working_directory=str(tmp_path),
        config=SessionConfig(system_prompt="test"),
    )
    assert sdk.storage_manager is None, "This test only means something with storage disabled"

    received = []

    async def callback(converted_message):
        received.append(converted_message)

    sdk.message_callback = callback

    frame = {
        "type": "assistant", "content": "hello", "timestamp": 1.0,
        "session_id": session_id,
    }
    await sdk._process_sdk_message(frame)

    assert len(received) == 1
    assert received[0].get("message_id"), (
        "record_id (message_id) must be stamped even when storage_manager is None"
    )
