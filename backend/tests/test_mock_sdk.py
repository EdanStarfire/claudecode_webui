"""
Tests for the Mock SDK Engine (issue #559).

Tests cover:
- SessionRecording: parsing, segment indexing, action classification
- ReplayEngine: timing, callback sequencing, action validation
- MockClaudeSDK: full lifecycle (start → send → receive → terminate)
- ActionMismatchError: wrong action type raises with clear message
- Permission granularity: allow vs allow+suggestions vs deny vs guidance
- Speed factor: 0.0 = instant
- Interrupt: cancel mid-replay
- Integration: MockClaudeSDK with message_callback capturing output
- SessionCoordinator injection: set_sdk_factory
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.mock_sdk import (
    ActionMismatchError,
    ActionType,
    MockClaudeSDK,
    ReplayEngine,
    SessionRecording,
)
from backend.session_config import SessionConfig

# Fixture directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ─────────────────────────────────────────────────
# SessionRecording Tests
# ─────────────────────────────────────────────────


class TestSessionRecording:
    """Tests for SessionRecording parsing and segmentation."""

    def test_load_single_turn(self):
        """Single-turn recording loads correctly."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        assert len(recording.messages) == 5
        assert recording.state.get("session_id") == "test-single-turn"

    def test_load_multi_turn(self):
        """Multi-turn recording loads correctly."""
        recording = SessionRecording(FIXTURES_DIR / "multi_turn")
        assert len(recording.messages) == 9

    def test_load_tool_use(self):
        """Tool-use recording loads correctly."""
        recording = SessionRecording(FIXTURES_DIR / "tool_use")
        assert len(recording.messages) == 7

    def test_missing_directory_raises(self):
        """Missing session directory raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            SessionRecording("/nonexistent/path")

    def test_single_turn_segments(self):
        """Single-turn: Segment 0 (client_launched), action (USER_MESSAGE), Segment 1 (init+assistant+result)."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        # Segment 0: client_launched system message
        assert recording.get_segment_count() == 2
        assert recording.get_action_count() == 1
        assert recording.get_expected_action(0) == ActionType.USER_MESSAGE

    def test_single_turn_segment0_content(self):
        """Segment 0 contains the client_launched system message."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        seg0 = recording.get_segment(0)
        assert len(seg0) == 1
        assert seg0[0].get("type") == "system"

    def test_single_turn_segment1_content(self):
        """Segment 1 contains init, assistant, and result messages."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        seg1 = recording.get_segment(1)
        assert len(seg1) == 3
        types = [recording._get_message_type(m) for m in seg1]
        assert types == ["system", "assistant", "result"]

    def test_multi_turn_segments(self):
        """Multi-turn: 3 segments, 2 user actions."""
        recording = SessionRecording(FIXTURES_DIR / "multi_turn")
        assert recording.get_segment_count() == 3
        assert recording.get_action_count() == 2
        assert recording.get_expected_action(0) == ActionType.USER_MESSAGE
        assert recording.get_expected_action(1) == ActionType.USER_MESSAGE

    def test_tool_result_classified_as_sdk(self):
        """Tool results (user type with tool_results) are SDK-generated."""
        recording = SessionRecording(FIXTURES_DIR / "tool_use")
        # Find the tool result message
        tool_result_msg = recording.messages[4]  # "Tool results: 1 results"
        assert recording._is_tool_result(tool_result_msg)
        assert recording._is_sdk_generated(tool_result_msg)

    def test_tool_use_segments(self):
        """Tool-use: tool_result is part of SDK segment, not a user action."""
        recording = SessionRecording(FIXTURES_DIR / "tool_use")
        assert recording.get_action_count() == 1
        assert recording.get_expected_action(0) == ActionType.USER_MESSAGE
        # Segment 1 should contain init, assistant (tool_use), tool_result, assistant, result
        seg1 = recording.get_segment(1)
        assert len(seg1) >= 4  # init + tool_use + tool_result + assistant + result

    def test_timestamp_extraction(self):
        """Timestamps are extracted correctly."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        seg0 = recording.get_segment(0)
        ts = recording.get_timestamp(seg0[0])
        assert ts == 1000000.0

    def test_out_of_bounds_segment(self):
        """Out-of-bounds segment index returns empty list."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        assert recording.get_segment(999) == []

    def test_out_of_bounds_action(self):
        """Out-of-bounds action index returns None."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        assert recording.get_expected_action(999) is None

    def test_legacy_format_detection(self):
        """Legacy/flat format messages (type: xxx) are detected correctly.

        Issue #2109 (AC11): every fixture record is now canonical flat shape
        (no more `_type`-tagged StoredMessage-era records)."""
        recording = SessionRecording(FIXTURES_DIR / "tool_use")
        assert recording._get_message_type(recording.messages[0]) == "system"
        assert recording._get_message_type(recording.messages[3]) == "assistant"

    def test_sdk_format_detection(self):
        """Flat-format messages originally produced from a reconstructed SDK message
        are detected correctly (issue #2109 AC11: no more `_type: XxxMessage` tagging)."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        # init message
        assert recording._get_message_type(recording.messages[2]) == "system"
        assert recording._get_message_type(recording.messages[3]) == "assistant"
        assert recording._get_message_type(recording.messages[4]) == "result"


# ─────────────────────────────────────────────────
# ReplayEngine Tests
# ─────────────────────────────────────────────────


class TestReplayEngine:
    """Tests for ReplayEngine timing and validation."""

    def test_action_validation_pass(self):
        """Matching action type passes validation."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        engine = ReplayEngine(recording, speed_factor=0.0)
        # Should not raise
        engine.validate_action(ActionType.USER_MESSAGE, "USER_MESSAGE", 0)

    def test_action_validation_fail(self):
        """Mismatched action type raises ActionMismatchError."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        engine = ReplayEngine(recording, speed_factor=0.0)
        with pytest.raises(ActionMismatchError) as exc_info:
            engine.validate_action(ActionType.USER_MESSAGE, "PERMISSION_DENY", 0)
        assert exc_info.value.segment_index == 0
        assert exc_info.value.expected == ActionType.USER_MESSAGE
        assert exc_info.value.got == "PERMISSION_DENY"

    def test_action_mismatch_error_message(self):
        """ActionMismatchError has clear message format."""
        err = ActionMismatchError(3, ActionType.PERMISSION_ALLOW, "PERMISSION_DENY")
        assert "segment 3" in str(err)
        assert "PERMISSION_ALLOW" in str(err)
        assert "PERMISSION_DENY" in str(err)

    @pytest.mark.asyncio
    async def test_replay_segment_fires_callbacks(self):
        """Replaying a segment fires message_callback for each message."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        received = []
        engine = ReplayEngine(
            recording,
            message_callback=lambda msg: received.append(msg),
            speed_factor=0.0,
        )
        await engine.replay_segment(0)
        assert len(received) == 1  # Segment 0: client_launched

    @pytest.mark.asyncio
    async def test_replay_segment_respects_order(self):
        """Messages are replayed in recorded order."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        received = []
        engine = ReplayEngine(
            recording,
            message_callback=lambda msg: received.append(msg),
            speed_factor=0.0,
        )
        await engine.replay_segment(1)
        types = [recording._get_message_type(m) for m in received]
        assert types == ["system", "assistant", "result"]

    @pytest.mark.asyncio
    async def test_replay_async_callback(self):
        """Async callbacks are awaited properly."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        received = []

        async def async_cb(msg):
            received.append(msg)

        engine = ReplayEngine(
            recording, message_callback=async_cb, speed_factor=0.0
        )
        await engine.replay_segment(0)
        assert len(received) == 1

    def test_interrupt(self):
        """Interrupt sets the interrupted flag."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        engine = ReplayEngine(recording, speed_factor=0.0)
        engine.interrupt()
        assert engine._interrupted

    @pytest.mark.asyncio
    async def test_replay_empty_segment(self):
        """Replaying out-of-bounds segment does nothing."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        received = []
        engine = ReplayEngine(
            recording,
            message_callback=lambda msg: received.append(msg),
            speed_factor=0.0,
        )
        await engine.replay_segment(999)
        assert len(received) == 0


# ─────────────────────────────────────────────────
# MockClaudeSDK Tests
# ─────────────────────────────────────────────────


class TestMockClaudeSDK:
    """Tests for MockClaudeSDK lifecycle and integration."""

    @pytest.mark.asyncio
    async def test_start_success(self):
        """Mock session starts successfully."""
        mock = MockClaudeSDK(
            session_id="test-1",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        result = await mock.start()
        assert result is True
        assert mock.is_running()
        await mock.terminate()

    @pytest.mark.asyncio
    async def test_start_missing_dir(self):
        """Mock session fails with missing session directory."""
        mock = MockClaudeSDK(
            session_id="test-bad",
            working_directory="/nonexistent",
            session_dir="/nonexistent",
            speed_factor=0.0,
        )
        result = await mock.start()
        assert result is False
        assert not mock.is_running()

    @pytest.mark.asyncio
    async def test_single_turn_lifecycle(self):
        """Full single-turn lifecycle: start → send → receive → terminate."""
        received = []

        async def on_message(msg):
            received.append(msg)

        mock = MockClaudeSDK(
            session_id="test-lifecycle",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            message_callback=on_message,
            speed_factor=0.0,
        )
        await mock.start()

        # Segment 0 is skipped (client_launched handled by SessionCoordinator)
        start_count = len(received)
        assert start_count == 0

        # Send a message
        await mock.send_message("Hello")

        # Should have received: user msg + init + assistant + result
        assert len(received) > start_count

        await mock.terminate()
        assert not mock.is_running()

    @pytest.mark.asyncio
    async def test_multi_turn_lifecycle(self):
        """Multi-turn: two send_message calls produce correct segments."""
        received = []

        async def on_message(msg):
            received.append(msg)

        mock = MockClaudeSDK(
            session_id="test-multi",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "multi_turn"),
            message_callback=on_message,
            speed_factor=0.0,
        )
        await mock.start()
        start_count = len(received)

        # First turn
        await mock.send_message("What is 2+2?")
        after_turn1 = len(received)
        assert after_turn1 > start_count

        # Second turn
        await mock.send_message("And what is 3+3?")
        after_turn2 = len(received)
        assert after_turn2 > after_turn1

        await mock.terminate()

    @pytest.mark.asyncio
    async def test_tool_use_lifecycle(self):
        """Tool-use session replays tool calls and results correctly."""
        received = []

        async def on_message(msg):
            received.append(msg)

        mock = MockClaudeSDK(
            session_id="test-tools",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "tool_use"),
            message_callback=on_message,
            speed_factor=0.0,
        )
        await mock.start()
        await mock.send_message("Read the file")

        # Should include tool_use and tool_result messages
        types = [m.get("type") or m.get("_type", "") for m in received]
        assert any("assistant" in t.lower() for t in types)

        await mock.terminate()

    @pytest.mark.asyncio
    async def test_get_info(self):
        """get_info returns dict with mock flag."""
        mock = MockClaudeSDK(
            session_id="test-info",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        await mock.start()
        info = mock.get_info()
        assert info["mock"] is True
        assert info["session_id"] == "test-info"
        await mock.terminate()

    @pytest.mark.asyncio
    async def test_get_queue_size(self):
        """Queue size is always 0 for mock."""
        mock = MockClaudeSDK(
            session_id="test-queue",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        assert mock.get_queue_size() == 0

    @pytest.mark.asyncio
    async def test_set_permission_mode(self):
        """set_permission_mode is a no-op that returns True."""
        mock = MockClaudeSDK(
            session_id="test-perm",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        result = await mock.set_permission_mode("bypassPermissions")
        assert result is True
        assert mock.current_permission_mode == "bypassPermissions"

    def test_timestamp_injection_attributes_default_without_config(self):
        """Issue #1779: MockClaudeSDK must expose the same timestamp-injection
        attributes as ClaudeSDK even with no config kwarg, since SessionCoordinator.
        send_message() reads them unconditionally on every call."""
        mock = MockClaudeSDK(
            session_id="test-tsinj-no-config",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        assert mock.inject_timestamps_enabled is False
        assert mock.timestamp_injection_frequency == "every_message"
        assert mock.timestamp_injection_timezone == "UTC"

    def test_timestamp_injection_attributes_from_config(self):
        """Config-derived values are surfaced the same way current_permission_mode/model are."""
        mock = MockClaudeSDK(
            session_id="test-tsinj-config",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
            config=SessionConfig(
                inject_timestamps_enabled=True,
                timestamp_injection_frequency="once_per_day",
                timestamp_injection_timezone="America/New_York",
            ),
        )
        assert mock.inject_timestamps_enabled is True
        assert mock.timestamp_injection_frequency == "once_per_day"
        assert mock.timestamp_injection_timezone == "America/New_York"

    @pytest.mark.asyncio
    async def test_interrupt_session(self):
        """Interrupt stops replay."""
        mock = MockClaudeSDK(
            session_id="test-interrupt",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        await mock.start()
        result = await mock.interrupt_session()
        assert result is True
        await mock.terminate()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Disconnect sets shutdown event."""
        mock = MockClaudeSDK(
            session_id="test-disconnect",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        await mock.start()
        result = await mock.disconnect()
        assert result is True
        assert mock._shutdown_event.is_set()
        await mock.terminate()

    @pytest.mark.asyncio
    async def test_send_before_start(self):
        """Sending message before start returns False."""
        mock = MockClaudeSDK(
            session_id="test-nostart",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        result = await mock.send_message("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_accepts_all_claude_sdk_kwargs(self):
        """MockClaudeSDK accepts all ClaudeSDK constructor kwargs without error."""
        config = SessionConfig(
            permission_mode="acceptEdits",
            system_prompt="test",
            override_system_prompt=True,
            allowed_tools=["bash"],
            disallowed_tools=[],
            model="claude-sonnet-4-5-20250929",
            sandbox_enabled=False,
            sandbox_config=None,
            setting_sources=None,
            cli_path=None,
            thinking_mode=None,
            thinking_budget_tokens=None,
            effort=None,
        )
        mock = MockClaudeSDK(
            session_id="test-kwargs",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            config=config,
            storage_manager=None,
            session_manager=None,
            message_callback=None,
            error_callback=None,
            permission_callback=None,
            resume_session_id=None,
            mcp_servers=None,
            experimental=False,
            stderr_callback=None,
            extra_env=None,
        )
        assert mock.session_id == "test-kwargs"
        assert mock.current_permission_mode == "acceptEdits"


# ─────────────────────────────────────────────────
# _converting_callback Unification Tests (issue #2084 stage 3-E)
# ─────────────────────────────────────────────────


class TestConvertingCallbackUnification:
    """_converting_callback must build the exact same canonical MessageRecord shape
    the real construction primitives (reconstruct_sdk_message + MessageRecord.
    from_sdk_message) would build directly — proving the unification onto those
    primitives didn't silently diverge from what it's reusing."""

    @pytest.mark.asyncio
    async def test_unrecognized_sdk_type_raises(self):
        """A `_type` that reconstruct_sdk_message() doesn't recognize (fixture-vs-
        installed-SDK drift) now raises loudly instead of degrading to an
        unconverted pass-through (issue #2109 AC11) — every committed fixture is
        confirmed canonical-shape clean, so this should never fire in practice.
        The caller (`_safe_callback`, in the real replay path) logs and drops it
        rather than crashing the whole replay."""
        received = []

        async def on_message(msg):
            received.append(msg)

        storage_manager = AsyncMock()
        mock = MockClaudeSDK(
            session_id="test-unknown-type",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            storage_manager=storage_manager,
            message_callback=on_message,
            speed_factor=0.0,
        )

        msg = {
            "_type": "SomeFutureMessageType",
            "timestamp": 1000000.0,
            "session_id": "test-unknown-type",
            "data": {"anything": "goes"},
        }
        with pytest.raises(RuntimeError, match="SomeFutureMessageType"):
            await mock._converting_callback(msg)

        assert received == []
        storage_manager.append_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sdk_tagged_message_matches_direct_construction(self):
        from backend.models.messages import MessageRecord
        from backend.raw_replay import reconstruct_sdk_message
        from backend.tests.test_synthetic_fixture_freshness import _strip_nondeterministic

        received = []

        async def on_message(msg):
            received.append(msg)

        mock = MockClaudeSDK(
            session_id="test-unify",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            message_callback=on_message,
            speed_factor=0.0,
        )

        sdk_msg = {
            "_type": "AssistantMessage",
            "timestamp": 1000012.5,
            "session_id": "test-unify",
            "data": {
                "content": [{"text": "I'm doing well! How can I help you today?"}],
                "model": "claude-sonnet-4-5-20250929",
            },
        }
        await mock._converting_callback(sdk_msg)
        assert len(received) == 1
        via_callback = dict(received[0])

        sdk_obj = reconstruct_sdk_message(sdk_msg["_type"], sdk_msg["data"])
        expected = MessageRecord.from_sdk_message(sdk_obj, session_id="test-unify").to_dict()

        # message_id/timestamp are freshly minted per call (the only fields
        # expected to differ between two independent calls to the same underlying
        # construction path) — reuse the same nondeterministic-key stripping the
        # fixture-freshness check already established, rather than a second
        # independent list of the same keys.
        assert _strip_nondeterministic(via_callback) == _strip_nondeterministic(expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fixture_name", ["hook_messages", "multi_turn", "permission_flow", "single_turn", "tool_use"]
    )
    async def test_fixture_round_trips_to_canonical_shape(self, fixture_name, tmp_path):
        """Replaying each non-raw_log.jsonl fixture through MockClaudeSDK into a
        fresh storage manager, then reading it back via the real
        SessionCoordinator.get_session_messages(), must yield only canonical
        MessageRecord shapes for every `_type`-tagged source record — the gap
        (production can't generate the legacy shape these fixtures used to produce)
        that motivated this unification."""
        from backend.data_storage import DataStorageManager
        from backend.session_coordinator import SessionCoordinator

        session_id = "test-roundtrip"
        coordinator = SessionCoordinator(tmp_path)
        storage_manager = DataStorageManager(tmp_path / "session_data")
        await storage_manager.initialize()
        coordinator._storage_managers[session_id] = storage_manager

        mock = MockClaudeSDK(
            session_id=session_id,
            working_directory=str(tmp_path),
            session_dir=str(FIXTURES_DIR / fixture_name),
            storage_manager=storage_manager,
            message_callback=lambda msg: None,
            speed_factor=0.0,
        )
        await mock.start()
        # Drive every remaining segment through send_message so the whole
        # fixture (not just segment 0, skipped like client_launched is in
        # production) gets replayed and stored. Each call injects its own
        # synthetic "continue" user message through the same canonical-record
        # path (counts toward canonical_records below); a permission-response
        # action auto-advances a further segment within the SAME call via
        # _handle_pending_permissions(), so the number of calls actually needed
        # is tracked by segment_cursor, not precomputed from segment_count.
        continue_calls = 0
        while mock._engine._segment_cursor < mock._recording.get_segment_count():
            await mock.send_message("continue")
            continue_calls += 1

        # Segment 0 is never replayed (mirrors production skipping client_launched).
        # Issue #2109 (AC11): every fixture record is now canonical flat shape (no
        # more `_type` tagging), and every record in a replayed segment — not just
        # ones that used to carry `_type` — gets individually converted and stored
        # by _converting_callback, so count the segments' full record total instead
        # of filtering on a key that no longer appears anywhere.
        replayed_segments = mock._recording.segments[1:]
        segment_record_count = sum(len(segment) for segment in replayed_segments)
        # Each non-guidance permission action fires one extra pass-through record
        # (permission_flow's reconstructed `running`/`denied` tool_call transition —
        # the action boundary itself, excluded from segment content, see
        # SessionRecording._is_permission_response) via
        # _handle_pending_permissions() — see mock_sdk.py.
        permission_action_count = sum(
            1
            for i in range(mock._recording.get_action_count())
            for action in [mock._recording.get_expected_action(i)]
            if action in (
                ActionType.PERMISSION_ALLOW,
                ActionType.PERMISSION_ALLOW_WITH_SUGGESTIONS,
                ActionType.PERMISSION_DENY,
            )
        )

        result = await coordinator.get_session_messages(session_id)
        canonical_records = [
            m for m in result["messages"] if "_type" not in m and m.get("message_id")
        ]
        # Exact count: every replayed-segment source record, plus the injected
        # "continue" message per send_message() call above, plus one pass-through
        # record per non-guidance permission action, plus the single
        # replay_complete system marker _emit_replay_complete() fires once all
        # segments are consumed (only when at least one send_message() call ran)
        # — an exact equality here (rather than a lower bound) is what actually
        # proves no replayed record is silently dropped.
        expected_count = (
            segment_record_count
            + continue_calls
            + permission_action_count
            + (1 if continue_calls else 0)
        )
        assert len(canonical_records) == expected_count, (
            f"Expected exactly {expected_count} canonical MessageRecord-shaped "
            f"records for fixture {fixture_name!r}, got {len(canonical_records)}"
        )
        # None of the replayed records should retain the legacy _type/data shape —
        # that's precisely the shape this unification eliminates.
        assert not any("_type" in m for m in result["messages"])


# ─────────────────────────────────────────────────
# Action Validation Tests
# ─────────────────────────────────────────────────


class TestActionValidation:
    """Tests for action type matching and mismatch errors."""

    @pytest.mark.asyncio
    async def test_wrong_action_type_raises(self):
        """Sending permission response when USER_MESSAGE expected raises."""
        mock = MockClaudeSDK(
            session_id="test-mismatch",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            speed_factor=0.0,
        )
        await mock.start()

        # The first action is USER_MESSAGE, so calling with wrong type should fail
        engine = mock._engine
        with pytest.raises(ActionMismatchError) as exc_info:
            engine.validate_action(
                ActionType.USER_MESSAGE, "PERMISSION_DENY", 0
            )
        assert "USER_MESSAGE" in str(exc_info.value)
        assert "PERMISSION_DENY" in str(exc_info.value)
        await mock.terminate()

    def test_all_action_types_exist(self):
        """All action types from the plan exist in the enum."""
        assert ActionType.USER_MESSAGE.value == "USER_MESSAGE"
        assert ActionType.PERMISSION_ALLOW.value == "PERMISSION_ALLOW"
        assert ActionType.PERMISSION_ALLOW_WITH_SUGGESTIONS.value == "PERMISSION_ALLOW_WITH_SUGGESTIONS"
        assert ActionType.PERMISSION_DENY.value == "PERMISSION_DENY"
        assert ActionType.PERMISSION_GUIDANCE.value == "PERMISSION_GUIDANCE"


# ─────────────────────────────────────────────────
# SessionCoordinator Injection Test
# ─────────────────────────────────────────────────


class TestSessionCoordinatorInjection:
    """Test that SessionCoordinator supports SDK factory injection."""

    def test_sdk_factory_default(self):
        """SessionCoordinator defaults to ClaudeSDK factory."""
        from backend.session_coordinator import SessionCoordinator

        coordinator = SessionCoordinator()
        assert coordinator._sdk_factory is SessionCoordinator._default_sdk_factory

    def test_set_sdk_factory(self):
        """set_sdk_factory replaces the factory."""
        from backend.session_coordinator import SessionCoordinator

        coordinator = SessionCoordinator()
        coordinator.set_sdk_factory(MockClaudeSDK)
        assert coordinator._sdk_factory is MockClaudeSDK
