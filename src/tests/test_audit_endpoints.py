"""Integration tests for audit REST endpoints."""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.analytics.database import AnalyticsDB


async def _insert_event(db, session_id="s1", event_type="tool_call", status="ok",
                         turn_id="s1:1", ts=None):
    ts = ts or time.time()
    sql = (
        "INSERT INTO audit_events "
        "(timestamp, source_ts, session_id, project_id, legion_id, turn_id, "
        "event_type, tool_name, status, summary, message_id, extra_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    await db.execute_write(sql, (ts, None, session_id, None, None, turn_id,
                                 event_type, "Edit", status, "test", None, None))


@pytest.fixture
async def app_with_audit(tmp_path):
    """Create a minimal FastAPI app with audit routes registered."""
    from fastapi import FastAPI

    from src.routers.audit import build_router

    db_path = tmp_path / "test_audit.db"
    db = AnalyticsDB(db_path)
    await db.initialize()

    webui = MagicMock()
    webui.analytics_db = db
    webui.audit_queue = MagicMock()
    webui.audit_queue.wait_for_events = AsyncMock()

    # minimal session manager mock
    sm = MagicMock()
    sm._active_sessions = {}
    webui.coordinator.session_manager = sm

    app = FastAPI()
    app.include_router(build_router(webui))

    yield app, db
    await db.close()


@pytest.mark.asyncio
async def test_get_audit_events_200(app_with_audit):
    app, db = app_with_audit
    await _insert_event(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/audit/events", params={"since": time.time() - 60})
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert len(data["events"]) >= 1


@pytest.mark.asyncio
async def test_get_audit_events_no_db():
    """Without analytics_db, endpoint returns 503."""
    from fastapi import FastAPI

    from src.routers.audit import build_router

    webui = MagicMock()
    webui.analytics_db = None
    webui.audit_queue = None

    app = FastAPI()
    app.include_router(build_router(webui))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/audit/events")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_get_audit_turns_200(app_with_audit):
    app, db = app_with_audit
    await _insert_event(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/audit/turns", params={"since": time.time() - 60})
    assert resp.status_code == 200
    data = resp.json()
    assert "turns" in data
    assert "standalones" in data


@pytest.mark.asyncio
async def test_get_audit_events_session_filter(app_with_audit):
    app, db = app_with_audit
    await _insert_event(db, session_id="s1")
    await _insert_event(db, session_id="s2")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/audit/events",
            params={"since": time.time() - 60, "session_ids": "s1"}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert all(e["session_id"] == "s1" for e in data["events"])


@pytest.mark.asyncio
async def test_poll_audit_200(app_with_audit):
    app, db = app_with_audit
    await _insert_event(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/poll/audit", params={"cursor": 0, "timeout": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
