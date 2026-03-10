"""
Tests for history_distiller.py - session history distillation into markdown.
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.history_distiller import distill_session_history


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _write_jsonl(path: Path, messages: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            json.dump(msg, f)
            f.write("\n")


@pytest.mark.asyncio
async def test_issue_691_basic_distillation(temp_dir):
    """Distill a mix of user, agent, and system messages."""
    messages = [
        {"type": "user", "content": "Hello", "timestamp": 1700000000.0, "metadata": {}},
        {"type": "assistant", "content": "Hi there", "timestamp": 1700000010.0, "metadata": {}},
        {"type": "system", "content": "Session started", "timestamp": 1700000020.0, "metadata": {}},
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "history" / "test.md"
    _write_jsonl(jsonl, messages)

    result = await distill_session_history(jsonl, output, "sess-1", "2024-01-01T00:00:00+00:00")
    assert result is True
    assert output.exists()

    content = output.read_text()
    assert "# Session History - sess-1" in content
    assert "## " in content
    assert "User" in content
    assert "Agent" in content
    assert "System" in content
    assert "Total messages: 3" in content
    assert "User messages: 1" in content
    assert "Agent messages: 1" in content
    assert "System messages: 1" in content


@pytest.mark.asyncio
async def test_issue_691_inbound_comm(temp_dir):
    """Inbound comms detected via metadata.comm."""
    messages = [
        {
            "type": "user",
            "content": "Task assigned",
            "timestamp": 1700000000.0,
            "metadata": {"comm": {"from_display_name": "Builder"}},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Comm (Inbound from Builder)" in content
    assert "Inbound: 1" in content
    assert "User messages: 0" in content


@pytest.mark.asyncio
async def test_issue_691_outbound_comm(temp_dir):
    """Outbound comms via send_comm tool_use."""
    messages = [
        {
            "type": "tool_use",
            "content": "",
            "timestamp": 1700000000.0,
            "metadata": {
                "tool_name": "mcp__legion__send_comm",
                "tool_input": {
                    "to_minion_name": "Reviewer",
                    "summary": "Done with task",
                    "content": "All tests pass",
                },
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Comm (Outbound to Reviewer)" in content
    assert "**Summary:** Done with task" in content
    assert "All tests pass" in content
    assert "Outbound: 1" in content


@pytest.mark.asyncio
async def test_issue_691_excluded_types(temp_dir):
    """Result, tool_result, permission_request, thinking messages are excluded."""
    messages = [
        {"type": "result", "content": "ok", "timestamp": 1700000000.0, "metadata": {}},
        {"type": "tool_result", "content": "output", "timestamp": 1700000001.0, "metadata": {}},
        {"type": "permission_request", "content": "allow?", "timestamp": 1700000002.0, "metadata": {}},
        {"type": "thinking", "content": "hmm", "timestamp": 1700000003.0, "metadata": {}},
        {"type": "user", "content": "real message", "timestamp": 1700000004.0, "metadata": {}},
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Total messages: 1" in content
    assert "real message" in content


@pytest.mark.asyncio
async def test_issue_691_excluded_system_subtypes(temp_dir):
    """System messages with excluded subtypes are filtered."""
    messages = [
        {
            "type": "system",
            "content": "task started",
            "timestamp": 1700000000.0,
            "metadata": {"subtype": "task_started"},
        },
        {
            "type": "system",
            "content": "status update",
            "timestamp": 1700000001.0,
            "metadata": {"subtype": "status_update"},
        },
        {
            "type": "system",
            "content": "important system msg",
            "timestamp": 1700000002.0,
            "metadata": {"subtype": "other"},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "System messages: 1" in content
    assert "important system msg" in content


@pytest.mark.asyncio
async def test_issue_691_malformed_jsonl(temp_dir):
    """Malformed lines are skipped gracefully."""
    jsonl = temp_dir / "messages.jsonl"
    with open(jsonl, "w") as f:
        f.write("not valid json\n")
        f.write('{"type": "user", "content": "valid", "timestamp": 1700000000.0, "metadata": {}}\n')
        f.write("{broken\n")

    output = temp_dir / "out.md"
    result = await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    assert result is True
    content = output.read_text()
    assert "Total messages: 1" in content


@pytest.mark.asyncio
async def test_issue_691_empty_input(temp_dir):
    """Empty messages.jsonl produces valid but empty markdown."""
    jsonl = temp_dir / "messages.jsonl"
    jsonl.write_text("")
    output = temp_dir / "out.md"

    result = await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    assert result is True
    content = output.read_text()
    assert "# Session History" in content
    assert "Total messages: 0" in content


@pytest.mark.asyncio
async def test_issue_691_missing_file(temp_dir):
    """Missing messages.jsonl returns False."""
    output = temp_dir / "out.md"
    result = await distill_session_history(
        temp_dir / "nonexistent.jsonl", output, "s1", "2024-01-01T00:00:00+00:00"
    )
    assert result is False
    assert not output.exists()


@pytest.mark.asyncio
async def test_issue_691_duration_calculation(temp_dir):
    """Session duration calculated from first to last timestamp."""
    messages = [
        {"type": "user", "content": "start", "timestamp": 1700000000.0, "metadata": {}},
        {"type": "assistant", "content": "end", "timestamp": 1700007200.0, "metadata": {}},
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "2h 0m" in content


@pytest.mark.asyncio
async def test_issue_691_structured_content(temp_dir):
    """Messages with list content blocks are extracted correctly."""
    messages = [
        {
            "type": "assistant",
            "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}],
            "timestamp": 1700000000.0,
            "metadata": {},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Hello\nWorld" in content


# --- StoredMessage format tests (issue #722) ---


@pytest.mark.asyncio
async def test_issue_722_stored_assistant_message(temp_dir):
    """StoredMessage AssistantMessage extracts text blocks, skips thinking."""
    messages = [
        {
            "_type": "AssistantMessage",
            "timestamp": 1700000000.0,
            "data": {
                "content": [
                    {"thinking": "Let me think about this..."},
                    {"text": "Here is my response."},
                    {"signature": "abc123"},
                    {"text": "And a follow-up."},
                ]
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Agent" in content
    assert "Here is my response." in content
    assert "And a follow-up." in content
    assert "thinking" not in content.split("---")[0]  # No thinking in entries
    assert "Agent messages: 1" in content


@pytest.mark.asyncio
async def test_issue_722_stored_assistant_empty_skipped(temp_dir):
    """StoredMessage AssistantMessage with only thinking blocks produces no entry."""
    messages = [
        {
            "_type": "AssistantMessage",
            "timestamp": 1700000000.0,
            "data": {
                "content": [
                    {"thinking": "Just thinking, no text output."},
                ]
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Total messages: 0" in content


@pytest.mark.asyncio
async def test_issue_722_stored_user_message(temp_dir):
    """StoredMessage UserMessage extracts text content."""
    messages = [
        {
            "_type": "UserMessage",
            "timestamp": 1700000000.0,
            "data": {
                "content": [
                    {"text": "Please fix the bug."},
                ]
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "User" in content
    assert "Please fix the bug." in content
    assert "User messages: 1" in content


@pytest.mark.asyncio
async def test_issue_722_stored_user_tool_result_skipped(temp_dir):
    """StoredMessage UserMessage with tool_use_id is a tool result, skipped."""
    messages = [
        {
            "_type": "UserMessage",
            "timestamp": 1700000000.0,
            "data": {
                "content": [
                    {"tool_use_id": "tool-123", "type": "tool_result", "content": "file contents"},
                ]
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Total messages: 0" in content


@pytest.mark.asyncio
async def test_issue_722_stored_tool_call_send_comm(temp_dir):
    """StoredMessage ToolCallUpdate for send_comm extracts outbound comm."""
    messages = [
        {
            "_type": "ToolCallUpdate",
            "timestamp": 1700000000.0,
            "data": {
                "name": "mcp__legion__send_comm",
                "input": {
                    "to_minion_name": "Reviewer",
                    "summary": "Build complete",
                    "content": "All tests passing.",
                },
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Comm (Outbound to Reviewer)" in content
    assert "**Summary:** Build complete" in content
    assert "All tests passing." in content
    assert "Outbound: 1" in content


@pytest.mark.asyncio
async def test_issue_722_stored_tool_call_non_comm_skipped(temp_dir):
    """StoredMessage ToolCallUpdate for non-comm tools is skipped."""
    messages = [
        {
            "_type": "ToolCallUpdate",
            "timestamp": 1700000000.0,
            "data": {
                "name": "Read",
                "input": {"file_path": "/some/file.py"},
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Total messages: 0" in content


@pytest.mark.asyncio
async def test_issue_722_stored_system_message(temp_dir):
    """StoredMessage SystemMessage applies exclusion filter."""
    messages = [
        {
            "_type": "SystemMessage",
            "timestamp": 1700000000.0,
            "data": {
                "subtype": "task_started",
                "content": [{"text": "should be excluded"}],
            },
        },
        {
            "_type": "SystemMessage",
            "timestamp": 1700000001.0,
            "data": {
                "subtype": "init",
                "content": [{"text": "Session initialized"}],
            },
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "System messages: 1" in content
    assert "Session initialized" in content
    assert "should be excluded" not in content


@pytest.mark.asyncio
async def test_issue_722_stored_skipped_types(temp_dir):
    """StoredMessage ResultMessage, TaskStartedMessage, PermissionRequestMessage are skipped."""
    messages = [
        {"_type": "ResultMessage", "timestamp": 1700000000.0, "data": {"content": "ok"}},
        {"_type": "TaskStartedMessage", "timestamp": 1700000001.0, "data": {}},
        {"_type": "TaskNotificationMessage", "timestamp": 1700000002.0, "data": {}},
        {"_type": "TaskProgressMessage", "timestamp": 1700000003.0, "data": {}},
        {"_type": "PermissionRequestMessage", "timestamp": 1700000004.0, "data": {}},
        {"_type": "PermissionResponseMessage", "timestamp": 1700000005.0, "data": {}},
        {
            "_type": "AssistantMessage",
            "timestamp": 1700000006.0,
            "data": {"content": [{"text": "real message"}]},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Total messages: 1" in content
    assert "real message" in content


@pytest.mark.asyncio
async def test_issue_722_mixed_legacy_and_stored(temp_dir):
    """Mixed legacy and StoredMessage formats are both handled correctly."""
    messages = [
        # Legacy format
        {"type": "user", "content": "Hello legacy", "timestamp": 1700000000.0, "metadata": {}},
        {"type": "assistant", "content": "Legacy reply", "timestamp": 1700000001.0, "metadata": {}},
        # StoredMessage format
        {
            "_type": "UserMessage",
            "timestamp": 1700000002.0,
            "data": {"content": [{"text": "Hello stored"}]},
        },
        {
            "_type": "AssistantMessage",
            "timestamp": 1700000003.0,
            "data": {"content": [{"text": "Stored reply"}]},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Hello legacy" in content
    assert "Legacy reply" in content
    assert "Hello stored" in content
    assert "Stored reply" in content
    assert "User messages: 2" in content
    assert "Agent messages: 2" in content
    assert "Total messages: 4" in content
