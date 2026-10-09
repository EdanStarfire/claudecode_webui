"""
Tests for history_distiller.py - session history distillation into markdown.
"""

import json
import tempfile
from pathlib import Path

import pytest

from backend.history_distiller import distill_session_history
from backend.models.messages import LegacyMessageFormatError


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


# --- Legacy `_type`-tagged StoredMessage shape: issue #2109 AC10 guard ---


@pytest.mark.asyncio
async def test_issue_2109_legacy_type_record_raises(temp_dir):
    """Any `_type`-tagged StoredMessage-era record raises LegacyMessageFormatError
    instead of being silently (mis)interpreted — issue #2109 AC10. StoredMessage-shape
    handling itself was deleted; canonical flat-shape coverage lives in the
    `test_issue_2084_flat_tool_call_*` and `test_issue_691_*` tests below."""
    messages = [
        {
            "_type": "AssistantMessage",
            "timestamp": 1700000000.0,
            "data": {"content": [{"text": "Here is my response."}]},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    with pytest.raises(LegacyMessageFormatError, match="sess-legacy"):
        await distill_session_history(jsonl, output, "sess-legacy", "2024-01-01T00:00:00+00:00")


@pytest.mark.asyncio
async def test_issue_2084_flat_tool_call_send_comm(temp_dir):
    """Issue #2084 (stage 3-D-prep, §5): a canonical flat `tool_call`-shaped
    mcp__legion__send_comm record must produce the same distilled entry as the
    equivalent legacy ToolCallUpdate-shaped one — before this stage, canonical
    tool_call records fell through every branch and were silently skipped."""
    messages = [
        {
            "type": "tool_call",
            "timestamp": 1700000000.0,
            "name": "mcp__legion__send_comm",
            "tool_use_id": "tu1",
            "status": "completed",
            "input": {
                "to_minion_name": "Reviewer",
                "summary": "Build complete",
                "content": "All tests passing.",
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
async def test_issue_2084_flat_tool_call_non_comm_skipped(temp_dir):
    """Canonical flat `tool_call` for a non-comm tool is skipped, mirroring the
    legacy tool_use and ToolCallUpdate branches' same behavior."""
    messages = [
        {
            "type": "tool_call",
            "timestamp": 1700000000.0,
            "name": "Read",
            "tool_use_id": "tu2",
            "status": "completed",
            "input": {"file_path": "/some/file.py"},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "out.md"
    _write_jsonl(jsonl, messages)

    await distill_session_history(jsonl, output, "s1", "2024-01-01T00:00:00+00:00")
    content = output.read_text()
    assert "Total messages: 0" in content


@pytest.mark.asyncio
async def test_issue_1628_slice_between_boundaries(temp_dir):
    """Distillation of a second compaction boundary excludes pre-first-boundary messages.

    Simulates a session with two compaction events. When the second boundary is
    distilled, only messages between the first and second boundary should appear.
    """
    ts_start = 1700000000.0
    ts_mid = 1700002000.0
    ts_second_boundary = 1700003000.0

    # Build a temp slice containing only messages from (first_boundary, second_boundary].
    messages_in_slice = [
        # This message is AFTER the first boundary — should appear
        {"type": "user", "content": "Post-first-boundary message", "timestamp": ts_mid, "metadata": {}},
        {"type": "assistant", "content": "Mid-session reply", "timestamp": ts_mid + 10, "metadata": {}},
        # The second boundary marker itself (canonical flat shape)
        {
            "type": "system",
            "content": "",
            "timestamp": ts_second_boundary,
            "metadata": {"subtype": "compact_boundary"},
        },
    ]
    # This message is BEFORE the first boundary — would be excluded by the slice helper
    pre_boundary_msg = {"type": "user", "content": "Pre-first-boundary message", "timestamp": ts_start, "metadata": {}}

    slice_file = temp_dir / "slice.jsonl"
    output = temp_dir / "history" / "second_boundary.md"
    # Write only the post-first-boundary messages (as _distill_compaction would do)
    _write_jsonl(slice_file, messages_in_slice)

    result = await distill_session_history(
        slice_file, output, "sess-slice", "2024-01-01T00:50:00+00:00"
    )
    assert result is True
    content = output.read_text()
    assert "Post-first-boundary message" in content
    assert "Mid-session reply" in content
    # Pre-boundary message must NOT appear (it was excluded by the slicer, not written to file)
    assert pre_boundary_msg["content"] not in content
    assert "User messages: 1" in content
    assert "Agent messages: 1" in content


@pytest.mark.asyncio
async def test_issue_1628_slice_from_session_start(temp_dir):
    """First compaction boundary: slice covers from session start (no prior boundary)."""
    messages = [
        {"type": "user", "content": "First user message", "timestamp": 1700000000.0, "metadata": {}},
        {"type": "assistant", "content": "First reply", "timestamp": 1700000010.0, "metadata": {}},
        {
            "type": "system",
            "content": "",
            "timestamp": 1700000100.0,
            "metadata": {"subtype": "compact_boundary"},
        },
    ]
    jsonl = temp_dir / "messages.jsonl"
    output = temp_dir / "first.md"
    _write_jsonl(jsonl, messages)

    result = await distill_session_history(jsonl, output, "sess-first", "2024-01-01T00:00:00+00:00")
    assert result is True
    content = output.read_text()
    assert "First user message" in content
    assert "First reply" in content
    assert "User messages: 1" in content
    assert "Agent messages: 1" in content
