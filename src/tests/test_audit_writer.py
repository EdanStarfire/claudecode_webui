"""Tests for AuditWriter."""
import asyncio

import pytest

from src.analytics.audit_writer import AuditWriter, _make_tool_summary


class MockDB:
    def __init__(self):
        self._initialized = True
        self.rows = []
        self._write_lock = asyncio.Lock()

    async def execute_write_many(self, sql, rows):
        self.rows.extend(rows)


@pytest.mark.asyncio
async def test_lifecycle_hook_produces_row():
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    await writer.on_session_state_change("s1", type("S", (), {"value": "active"})())
    await asyncio.sleep(0.3)  # let flush loop run
    await writer.stop()
    assert len(db.rows) >= 1
    row = db.rows[0]
    assert row[5] is None  # turn_id = None for lifecycle
    assert row[6] == "lifecycle"
    assert row[8] == "active"  # status


@pytest.mark.asyncio
async def test_user_message_opens_turn():
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    msg = {
        "type": "user",
        "timestamp": 1000.0,
        "message_id": "m1",
        "metadata": {},
    }
    await writer.on_message_append("s1", "proj1", msg)
    # The user message itself doesn't emit a row (no event for user msgs),
    # but a turn should be open now
    assert writer._tracker.current_turn_id("s1") == "s1:1"
    await writer.stop()


@pytest.mark.asyncio
async def test_tool_use_row():
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    # Open a turn first
    await writer.on_message_append("s1", "proj1", {"type": "user", "timestamp": 1.0, "message_id": "m0", "metadata": {}})
    msg = {
        "type": "tool_use",
        "timestamp": 2.0,
        "message_id": "m1",
        "metadata": {"tool_name": "Edit", "tool_use_id": "t1", "tool_input": {"file_path": "foo.py"}},
    }
    await writer.on_message_append("s1", "proj1", msg)
    await asyncio.sleep(0.3)
    await writer.stop()
    types = [r[6] for r in db.rows]
    assert "tool_call" in types


@pytest.mark.asyncio
async def test_watchdog_alert():
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    alert = {
        "session_id": "s1",
        "project_id": "p1",
        "watchdog": "idle_timeout",
        "details": {"idle_seconds": 60},
    }
    await writer.on_watchdog_alert(alert)
    await asyncio.sleep(0.3)
    await writer.stop()
    row = db.rows[0]
    assert row[6] == "watchdog"
    assert row[8] == "alert"


@pytest.mark.asyncio
async def test_none_db_is_noop():
    writer = AuditWriter(None)
    writer.start()
    # Should not raise
    await writer.on_session_state_change("s1", type("S", (), {"value": "active"})())
    await writer.on_watchdog_alert({"session_id": "s1", "project_id": None, "watchdog": "x", "details": {}})
    await writer.stop()


def test_make_tool_summary_edit():
    s = _make_tool_summary("Edit", {"file_path": "src/foo.py"})
    assert "Edit" in s
    assert "src/foo.py" in s


def test_make_tool_summary_bash():
    s = _make_tool_summary("Bash", {"command": "git status"})
    assert "Bash" in s
    assert "git status" in s


def test_make_tool_summary_unknown():
    s = _make_tool_summary("Unknown", {})
    assert s == "Unknown"


@pytest.mark.asyncio
async def test_comm_hook():
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    comm = {
        "comm_id": "c1",
        "comm_type": "TASK",
        "from_minion_id": "m1",
        "to_minion_id": "m2",
        "summary": "do the thing",
        "timestamp": 100.0,
    }
    await writer.on_comm("m1", "proj1", "legion1", comm)
    await asyncio.sleep(0.3)
    await writer.stop()
    row = db.rows[0]
    assert row[6] == "comm"
    assert row[4] == "legion1"  # legion_id


# ------------------------------------------------------------------
# Issue #1159: is_processing deduplication tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifecycle_deduplication_skips_identical():
    """Calling on_session_state_change twice with same args emits only one row."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    state = type("S", (), {"value": "active"})()
    await writer.on_session_state_change("s1", state)
    await writer.on_session_state_change("s1", state)  # duplicate — must be skipped
    await asyncio.sleep(0.3)
    await writer.stop()
    lifecycle_rows = [r for r in db.rows if r[6] == "lifecycle"]
    assert len(lifecycle_rows) == 1


@pytest.mark.asyncio
async def test_lifecycle_is_processing_change_emits_new_row():
    """State unchanged but is_processing flip must produce a second row."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    state = type("S", (), {"value": "active"})()
    await writer.on_session_state_change("s1", state, is_processing=False)
    await writer.on_session_state_change("s1", state, is_processing=True)
    await asyncio.sleep(0.3)
    await writer.stop()
    lifecycle_rows = [r for r in db.rows if r[6] == "lifecycle"]
    assert len(lifecycle_rows) == 2


@pytest.mark.asyncio
async def test_lifecycle_extra_includes_is_processing():
    """extra_json must contain is_processing field."""
    import json
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    state = type("S", (), {"value": "active"})()
    await writer.on_session_state_change("s1", state, is_processing=True)
    await asyncio.sleep(0.3)
    await writer.stop()
    row = db.rows[0]
    extra = json.loads(row[11])  # extra_json column
    assert extra.get("is_processing") is True


# ------------------------------------------------------------------
# Issue #1160: ToolCallUpdate audit event tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_update_pending_emits_started():
    """ToolCallUpdate with status=pending emits tool_call/started."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    msg = {
        "_type": "ToolCallUpdate",
        "timestamp": 10.0,
        "data": {
            "tool_use_id": "tu1",
            "name": "Edit",
            "status": "pending",
            "input": {"file_path": "foo.py"},
        },
    }
    await writer.on_message_append("s1", "proj1", msg)
    await asyncio.sleep(0.3)
    await writer.stop()
    tool_rows = [r for r in db.rows if r[6] == "tool_call"]
    assert len(tool_rows) == 1
    assert tool_rows[0][7] == "Edit"   # tool_name
    assert tool_rows[0][8] == "started"  # status


@pytest.mark.asyncio
async def test_tool_call_update_completed_emits_ok():
    """ToolCallUpdate with status=completed emits tool_call/ok."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    msg = {
        "_type": "ToolCallUpdate",
        "timestamp": 20.0,
        "data": {
            "tool_use_id": "tu2",
            "name": "Bash",
            "status": "completed",
            "input": {},
        },
    }
    await writer.on_message_append("s1", "proj1", msg)
    await asyncio.sleep(0.3)
    await writer.stop()
    tool_rows = [r for r in db.rows if r[6] == "tool_call"]
    assert len(tool_rows) == 1
    assert tool_rows[0][8] == "ok"


@pytest.mark.asyncio
async def test_tool_call_update_awaiting_permission_emits_permission():
    """ToolCallUpdate with status=awaiting_permission emits permission/requested."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    msg = {
        "_type": "ToolCallUpdate",
        "timestamp": 30.0,
        "data": {
            "tool_use_id": "tu3",
            "name": "Write",
            "status": "awaiting_permission",
            "input": {},
        },
    }
    await writer.on_message_append("s1", "proj1", msg)
    await asyncio.sleep(0.3)
    await writer.stop()
    perm_rows = [r for r in db.rows if r[6] == "permission"]
    assert len(perm_rows) == 1
    assert perm_rows[0][7] == "Write"   # tool_name
    assert perm_rows[0][8] == "requested"


@pytest.mark.asyncio
async def test_tool_call_update_denied_emits_permission_denied():
    """ToolCallUpdate with status=denied emits permission/denied."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    msg = {
        "_type": "ToolCallUpdate",
        "timestamp": 40.0,
        "data": {
            "tool_use_id": "tu4",
            "name": "Write",
            "status": "denied",
            "input": {},
        },
    }
    await writer.on_message_append("s1", "proj1", msg)
    await asyncio.sleep(0.3)
    await writer.stop()
    perm_rows = [r for r in db.rows if r[6] == "permission"]
    assert len(perm_rows) == 1
    assert perm_rows[0][8] == "denied"


@pytest.mark.asyncio
async def test_tool_call_update_running_emits_no_row():
    """ToolCallUpdate with status=running is intentionally skipped (noise)."""
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    msg = {
        "_type": "ToolCallUpdate",
        "timestamp": 50.0,
        "data": {
            "tool_use_id": "tu5",
            "name": "Read",
            "status": "running",
            "input": {},
        },
    }
    await writer.on_message_append("s1", "proj1", msg)
    await asyncio.sleep(0.3)
    await writer.stop()
    assert len(db.rows) == 0


# ------------------------------------------------------------------
# Issue #1161: on_comm uses minion names instead of session IDs
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1161_on_comm_uses_minion_names():
    """When from_minion_name/to_minion_name are in comm_data, extra uses names and summary uses names not IDs."""
    import json
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    comm = {
        "comm_id": "c2",
        "comm_type": "REPORT",
        "from_minion_id": "uuid-aaa",
        "to_minion_id": "uuid-bbb",
        "from_minion_name": "Overseer",
        "to_minion_name": "IssueManager",
        "summary": "task done",
        "timestamp": 200.0,
    }
    await writer.on_comm("uuid-aaa", "proj1", "legion1", comm)
    await asyncio.sleep(0.3)
    await writer.stop()
    assert len(db.rows) == 1
    row = db.rows[0]
    extra = json.loads(row[11])
    assert extra["from_name"] == "Overseer"
    assert extra["to_name"] == "IssueManager"
    # summary field should contain names, not raw UUIDs
    summary = row[9]
    assert "Overseer" in summary
    assert "IssueManager" in summary
    assert "uuid-aaa" not in summary
    assert "uuid-bbb" not in summary


# ------------------------------------------------------------------
# Issue #1162: on_comm stores comm_summary without routing prefix
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1162_on_comm_stores_comm_summary():
    """extra['comm_summary'] contains just the comm text; summary field has the formatted routing string."""
    import json
    db = MockDB()
    writer = AuditWriter(db)
    writer.start()
    comm = {
        "comm_id": "c3",
        "comm_type": "TASK",
        "from_minion_id": "uuid-ccc",
        "to_minion_id": "uuid-ddd",
        "from_minion_name": "Builder",
        "to_minion_name": "Reviewer",
        "summary": "please review the PR",
        "timestamp": 300.0,
    }
    await writer.on_comm("uuid-ccc", "proj1", "legion1", comm)
    await asyncio.sleep(0.3)
    await writer.stop()
    assert len(db.rows) == 1
    row = db.rows[0]
    extra = json.loads(row[11])
    # comm_summary is just the actual text, no routing prefix
    assert extra["comm_summary"] == "please review the PR"
    # summary field has the formatted string with routing
    summary = row[9]
    assert "→" in summary
    assert "please review the PR" in summary
