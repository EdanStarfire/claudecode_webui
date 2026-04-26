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
