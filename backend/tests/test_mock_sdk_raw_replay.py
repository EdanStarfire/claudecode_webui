"""Tests for mock_sdk.py's raw-layer replay mode (issue #1998, T3, AC6).

US5: a builder with no Anthropic credentials constructs SDK message types
directly (here, via SessionRecorder — the same class a live recording session
uses) and drives them through the recorder/raw-replay path in unit tests, to
validate mechanics before the owner ever records anything real.
"""

from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, UserMessage

from backend.mock_sdk import MockClaudeSDK, RawFixtureReplay
from backend.session_recorder import SessionRecorder


def _build_raw_fixture(session_dir) -> None:
    """Write a small raw_log.jsonl fixture using the real SessionRecorder —
    exactly what a live recording session would produce, per US5."""
    recorder = SessionRecorder("sess-1", session_dir)
    recorder.record_sdk_message(SystemMessage(subtype="init", data={"session_id": "sess-1"}))
    recorder.record_sdk_message(
        AssistantMessage(content=[TextBlock(text="hi")], model="claude-sonnet-4-5", session_id="sess-1")
    )
    recorder.record_sdk_message(UserMessage(content="thanks"))
    recorder.record_sdk_message(
        ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8,
            is_error=False, num_turns=1, session_id="sess-1",
        )
    )
    # Non-sdk_message kinds must be ignored by RawFixtureReplay/raw replay mode.
    recorder.record_interrupt()
    recorder.record_lifecycle("start")


class TestRawFixtureReplay:
    def test_parses_only_sdk_message_records_in_order(self, tmp_path):
        _build_raw_fixture(tmp_path)
        replay = RawFixtureReplay(tmp_path)
        assert [type(m).__name__ for m in replay.messages] == [
            "SystemMessage", "AssistantMessage", "UserMessage", "ResultMessage",
        ]

    def test_missing_raw_log_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            RawFixtureReplay(tmp_path)


class TestMockClaudeSDKRawMode:
    @pytest.mark.asyncio
    async def test_selects_raw_mode_from_directory_contents(self, tmp_path):
        _build_raw_fixture(tmp_path)
        sdk = MockClaudeSDK(session_id="sess-1", working_directory=str(tmp_path), session_dir=str(tmp_path))
        assert sdk._raw_mode is True

    @pytest.mark.asyncio
    async def test_storage_layer_fixture_is_not_raw_mode(self, tmp_path):
        (tmp_path / "messages.jsonl").write_text("")
        (tmp_path / "state.json").write_text("{}")
        sdk = MockClaudeSDK(session_id="sess-1", working_directory=str(tmp_path), session_dir=str(tmp_path))
        assert sdk._raw_mode is False

    @pytest.mark.asyncio
    async def test_start_drives_messages_through_real_wrapper_path(self, tmp_path):
        """Proves raw replay goes through ClaudeSDK._process_sdk_message's real
        conversion + storage pipeline, not mock_sdk's hand-rolled
        _convert_fixture_message()."""
        _build_raw_fixture(tmp_path)
        received = []

        async def message_callback(msg):
            received.append(msg)

        storage_manager = AsyncMock()
        sdk = MockClaudeSDK(
            session_id="sess-1",
            working_directory=str(tmp_path),
            session_dir=str(tmp_path),
            message_callback=message_callback,
            storage_manager=storage_manager,
        )

        result = await sdk.start()

        assert result is True
        # Real _convert_sdk_message() output shape: legacy "type" dicts (system/
        # assistant/user/result), not the mock's own hand-rolled converter output.
        assert [m.get("type") for m in received] == ["system", "assistant", "user", "result"]

        # Real _store_sdk_message() took the dataclass-faithful StoredMessage branch
        # (sdk_message_to_stored), proven by the "_type" discriminator it stamps.
        stored_types = [call.args[0].get("_type") for call in storage_manager.append_message.call_args_list]
        assert stored_types == ["SystemMessage", "AssistantMessage", "UserMessage", "ResultMessage"]

    @pytest.mark.asyncio
    async def test_send_message_is_a_safe_noop_in_raw_mode(self, tmp_path):
        """T3 scope is mechanics-only (see _start_raw_replay's docstring) — no
        interactive action-boundary replay yet, but send_message must not crash."""
        _build_raw_fixture(tmp_path)
        sdk = MockClaudeSDK(session_id="sess-1", working_directory=str(tmp_path), session_dir=str(tmp_path))
        await sdk.start()
        assert await sdk.send_message("anything") is True
