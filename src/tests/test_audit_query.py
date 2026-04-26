"""Tests for AuditQueryService."""
import time

import pytest

from src.analytics.audit_query import AuditQueryService, _parse_sparkline
from src.analytics.database import AnalyticsDB


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "audit_test.db"
    db = AnalyticsDB(path)
    await db.initialize()
    yield db
    await db.close()


async def _insert(db, session_id="s1", project_id="p1", event_type="tool_call",
                  tool_name="Edit", status="ok", turn_id="s1:1",
                  ts=None):
    ts = ts or time.time()
    sql = (
        "INSERT INTO audit_events "
        "(timestamp, source_ts, session_id, project_id, legion_id, turn_id, "
        "event_type, tool_name, status, summary, message_id, extra_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    await db.execute_write(sql, (ts, None, session_id, project_id, None, turn_id,
                                 event_type, tool_name, status, "test summary", None, None))


@pytest.mark.asyncio
async def test_query_events_basic(db):
    await _insert(db)
    qs = AuditQueryService(db)
    result = await qs.query_events(since=time.time() - 60)
    assert len(result["events"]) == 1
    ev = result["events"][0]
    assert ev["session_id"] == "s1"
    assert ev["event_type"] == "tool_call"
    assert ev["extra"] is None


@pytest.mark.asyncio
async def test_query_events_session_filter(db):
    await _insert(db, session_id="s1")
    await _insert(db, session_id="s2")
    qs = AuditQueryService(db)
    result = await qs.query_events(since=time.time() - 60, session_ids=["s1"])
    assert all(e["session_id"] == "s1" for e in result["events"])


@pytest.mark.asyncio
async def test_query_events_event_type_filter(db):
    await _insert(db, event_type="tool_call")
    await _insert(db, event_type="lifecycle")
    qs = AuditQueryService(db)
    result = await qs.query_events(since=time.time() - 60, event_types=["lifecycle"])
    assert all(e["event_type"] == "lifecycle" for e in result["events"])


@pytest.mark.asyncio
async def test_query_events_cursor(db):
    base = time.time()
    await _insert(db, ts=base - 10)
    await _insert(db, ts=base - 5)
    await _insert(db, ts=base - 1)
    qs = AuditQueryService(db)
    result = await qs.query_events(cursor=base - 6)
    assert len(result["events"]) == 2
    # cursor mode returns ASC
    assert result["events"][0]["timestamp"] <= result["events"][-1]["timestamp"]


@pytest.mark.asyncio
async def test_query_turns_basic(db):
    ts = time.time()
    await _insert(db, session_id="s1", turn_id="s1:1", ts=ts - 10)
    await _insert(db, session_id="s1", turn_id="s1:1", event_type="lifecycle", tool_name=None, ts=ts - 9)
    qs = AuditQueryService(db)
    result = await qs.query_turns(since=ts - 60)
    assert len(result["turns"]) == 1
    turn = result["turns"][0]
    assert turn["turn_id"] == "s1:1"
    assert turn["event_count"] == 2


@pytest.mark.asyncio
async def test_query_turns_standalone(db):
    ts = time.time()
    await _insert(db, turn_id=None, event_type="lifecycle", tool_name=None, ts=ts - 5)
    qs = AuditQueryService(db)
    result = await qs.query_turns(since=ts - 60)
    assert len(result["turns"]) == 0
    assert len(result["standalones"]) == 1


@pytest.mark.asyncio
async def test_total_estimate(db):
    for _ in range(5):
        await _insert(db)
    qs = AuditQueryService(db)
    result = await qs.query_events(since=time.time() - 60)
    assert result["total_estimate"] == 5


def test_parse_sparkline():
    raw = "tool_call:ok,tool_call:error,permission:denied,lifecycle:completed"
    result = _parse_sparkline(raw)
    assert result == ["tool", "error", "denied", "completed"]


def test_parse_sparkline_empty():
    assert _parse_sparkline("") == []
