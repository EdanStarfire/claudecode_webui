"""Tests for SessionRecorder (issue #1998, T1 — round-trip fidelity, AC2)."""

import dataclasses
import json

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from backend.raw_replay import reconstruct_sdk_message
from backend.session_recorder import SessionRecorder


def _read_records(recorder: SessionRecorder) -> list[dict]:
    return [
        json.loads(line)
        for line in recorder.log_path.read_text(encoding="utf-8").strip().splitlines()
        if line.strip()
    ]


class TestRoundTripFidelity:
    """T1: for every genuine SDK dataclass type, capture then reconstruct must
    round-trip to an equal object (AC2)."""

    @pytest.fixture
    def recorder(self, tmp_path):
        return SessionRecorder("sess-1", tmp_path)

    def _round_trip(self, recorder, sdk_message):
        recorder.record_sdk_message(sdk_message)
        record = _read_records(recorder)[-1]
        assert record["kind"] == "sdk_message"
        return reconstruct_sdk_message(record["_type"], record["data"])

    def test_assistant_message_with_text_and_tool_use(self, recorder):
        msg = AssistantMessage(
            content=[
                TextBlock(text="hello"),
                ToolUseBlock(id="tu_1", name="Read", input={"file_path": "/tmp/x"}),
            ],
            model="claude-sonnet-4-5",
            session_id="sess-1",
            uuid="u1",
        )
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_user_message_with_tool_result(self, recorder):
        msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="tu_1", content="ok", is_error=False)],
            uuid="u2",
        )
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_user_message_plain_string_content(self, recorder):
        msg = UserMessage(content="hi there")
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_system_message(self, recorder):
        msg = SystemMessage(subtype="init", data={"session_id": "sess-1"})
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_result_message_with_deferred_tool_use(self, recorder):
        from claude_agent_sdk import DeferredToolUse

        msg = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            deferred_tool_use=DeferredToolUse(id="tu_2", name="Bash", input={"command": "ls"}),
        )
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_stream_event(self, recorder):
        msg = StreamEvent(
            uuid="u3", session_id="sess-1", event={"type": "content_block_delta"}
        )
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_rate_limit_event(self, recorder):
        msg = RateLimitEvent(
            rate_limit_info=RateLimitInfo(status="allowed_warning", utilization=0.9),
            uuid="u4",
            session_id="sess-1",
        )
        result = self._round_trip(recorder, msg)
        assert dataclasses.asdict(result) == dataclasses.asdict(msg)

    def test_result_error_exception_shape(self, recorder):
        """ResultError isn't a dataclass — distinct exception-shape capture path."""
        err = ResultError(
            "API Error: overloaded",
            data={"subtype": "success", "result": "API Error: overloaded"},
            exit_code=1,
        )
        recorder.record_sdk_message(err)
        record = _read_records(recorder)[-1]
        assert record["_type"] == "ResultError"

        rebuilt = reconstruct_sdk_message(record["_type"], record["data"])
        assert isinstance(rebuilt, ResultError)
        assert rebuilt.subtype == err.subtype
        assert rebuilt.result == err.result
        assert rebuilt.exit_code == err.exit_code

    def test_unknown_type_raises(self, recorder):
        with pytest.raises(ValueError, match="Unknown SDK message type"):
            reconstruct_sdk_message("SomeFutureMessageType", {})


class TestCapturePoints:
    """Non-sdk_message capture kinds write the expected record shape."""

    @pytest.fixture
    def recorder(self, tmp_path):
        return SessionRecorder("sess-1", tmp_path)

    def test_permission_invocation_and_response(self, recorder):
        recorder.record_permission_invocation("Bash", {"command": "ls"}, suggestions=None)
        recorder.record_permission_response("Bash", "allow", None)
        records = _read_records(recorder)
        assert records[0]["kind"] == "permission_invocation"
        assert records[0]["tool_name"] == "Bash"
        assert records[1]["kind"] == "permission_response"
        assert records[1]["decision"] == "allow"

    def test_interrupt(self, recorder):
        recorder.record_interrupt()
        assert _read_records(recorder)[0]["kind"] == "interrupt"

    def test_lifecycle(self, recorder):
        recorder.record_lifecycle("restart")
        record = _read_records(recorder)[0]
        assert record["kind"] == "lifecycle"
        assert record["action"] == "restart"

    def test_queue_event(self, recorder):
        recorder.record_queue_event({"type": "comm_received", "data": {}})
        record = _read_records(recorder)[0]
        assert record["kind"] == "queue_event"
        assert record["event"]["type"] == "comm_received"

    def test_every_record_has_timestamp(self, recorder):
        recorder.record_interrupt()
        assert "timestamp" in _read_records(recorder)[0]

    def test_write_failure_does_not_raise(self, recorder, tmp_path):
        """A broken log path must never break the caller (AC7: no impact on the
        rest of the session even if recording itself misbehaves)."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        recorder.log_path = blocker / "raw_log.jsonl"
        recorder.record_interrupt()  # must not raise
