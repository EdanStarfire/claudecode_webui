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

import pytest

from src.mock_sdk import (
    ActionMismatchError,
    ActionType,
    MockClaudeSDK,
    ReplayEngine,
    SessionRecording,
)

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
        assert types == ["SystemMessage", "AssistantMessage", "ResultMessage"]

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
        """Legacy format messages (type: xxx) are detected correctly."""
        recording = SessionRecording(FIXTURES_DIR / "tool_use")
        # First message is legacy format
        assert recording._get_message_type(recording.messages[0]) == "system"
        # Assistant tool_use is also legacy format
        assert recording._get_message_type(recording.messages[3]) == "assistant"

    def test_sdk_format_detection(self):
        """SDK format messages (_type: XxxMessage) are detected correctly."""
        recording = SessionRecording(FIXTURES_DIR / "single_turn")
        # init message is SDK format
        assert recording._get_message_type(recording.messages[2]) == "SystemMessage"
        assert recording._get_message_type(recording.messages[3]) == "AssistantMessage"
        assert recording._get_message_type(recording.messages[4]) == "ResultMessage"


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
        assert types == ["SystemMessage", "AssistantMessage", "ResultMessage"]

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

        # Segment 0 replayed on start (client_launched)
        start_count = len(received)
        assert start_count >= 1

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
        mock = MockClaudeSDK(
            session_id="test-kwargs",
            working_directory="/tmp/test",
            session_dir=str(FIXTURES_DIR / "single_turn"),
            storage_manager=None,
            session_manager=None,
            message_callback=None,
            error_callback=None,
            permission_callback=None,
            permissions="acceptEdits",
            system_prompt="test",
            override_system_prompt=True,
            tools=["bash"],
            disallowed_tools=[],
            model="claude-sonnet-4-5-20250929",
            resume_session_id=None,
            mcp_servers=None,
            sandbox_enabled=False,
            sandbox_config=None,
            setting_sources=None,
            experimental=False,
            cli_path=None,
            stderr_callback=None,
            extra_env=None,
            thinking_mode=None,
            thinking_budget_tokens=None,
            effort=None,
        )
        assert mock.session_id == "test-kwargs"
        assert mock.current_permission_mode == "acceptEdits"


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
        from src.claude_sdk import ClaudeSDK
        from src.session_coordinator import SessionCoordinator

        coordinator = SessionCoordinator()
        assert coordinator._sdk_factory is ClaudeSDK

    def test_set_sdk_factory(self):
        """set_sdk_factory replaces the factory."""
        from src.session_coordinator import SessionCoordinator

        coordinator = SessionCoordinator()
        coordinator.set_sdk_factory(MockClaudeSDK)
        assert coordinator._sdk_factory is MockClaudeSDK
