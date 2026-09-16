"""Integration tests for GET /api/analytics/usage endpoint (issue #1132)."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.analytics.database import AnalyticsDB

_SONNET = "claude-sonnet-4-6"
_HAIKU = "claude-haiku-4-5"
_BASE_TS = 1705320000.0  # 2024-01-15 12:00:00 UTC
_DAY = 86400.0
_HOUR = 3600.0


async def _seed_turn(db: AnalyticsDB, session_id: str, turn_seq: int, **kwargs) -> None:
    sql = """
        INSERT INTO turn_usage
            (session_id, turn_seq, model, input_tokens, output_tokens,
             cache_write_tokens, cache_read_tokens, sdk_total_cost_usd, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, turn_seq) DO NOTHING
    """
    await db.execute_write(sql, (
        session_id,
        turn_seq,
        kwargs.get("model", _SONNET),
        kwargs.get("input_tokens", 0),
        kwargs.get("output_tokens", 0),
        kwargs.get("cache_write_tokens", 0),
        kwargs.get("cache_read_tokens", 0),
        kwargs.get("sdk_total_cost_usd"),
        kwargs.get("ts", _BASE_TS),
    ))


async def _mark_deleted(db: AnalyticsDB, session_id: str, deleted_at: float = _BASE_TS) -> None:
    """Insert/update a session_usage row with deleted_at set (issue #1941)."""
    await db.execute_write(
        """
        INSERT INTO session_usage (session_id, turn_count, last_updated, deleted_at)
        VALUES (?, 0, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET deleted_at = excluded.deleted_at
        """,
        (session_id, deleted_at, deleted_at),
    )


@pytest.fixture
async def app_and_db(tmp_path):
    from fastapi import FastAPI

    from backend.routers.analytics import build_router

    db = AnalyticsDB(tmp_path / "analytics.db")
    await db.initialize()

    sm = MagicMock()
    sm.get_session_info = AsyncMock(return_value=None)  # all sessions appear as "(deleted)" by default

    webui = MagicMock()
    webui.analytics_db = db
    webui.coordinator.session_manager = sm

    app = FastAPI()
    app.include_router(build_router(webui))

    yield app, db, sm
    await db.close()


# ---------------------------------------------------------------------------
# Basic response shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_by_session_200(app_and_db):
    app, db, _ = app_and_db
    await _seed_turn(db, "s1", 1, input_tokens=1000, ts=_BASE_TS)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["group_by"] == "session"
    assert "rows" in data
    assert "totals" in data
    assert len(data["rows"]) == 1


@pytest.mark.asyncio
async def test_group_by_hour_200(app_and_db):
    app, db, _ = app_and_db
    await _seed_turn(db, "s1", 1, input_tokens=500, ts=_BASE_TS)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "hour",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["group_by"] == "hour"
    assert "buckets" in data
    assert "totals" in data
    assert len(data["buckets"]) == 1


@pytest.mark.asyncio
async def test_group_by_day_200(app_and_db):
    app, db, _ = app_and_db
    await _seed_turn(db, "s1", 1, ts=_BASE_TS)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "day",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    assert resp.status_code == 200
    assert resp.json()["group_by"] == "day"


@pytest.mark.asyncio
async def test_invalid_group_by_422(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={"group_by": "week"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_group_by_422(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Empty range
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_range_session(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS + _DAY * 100),
            "until": int(_BASE_TS + _DAY * 101),
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] == []
    assert data["totals"]["session_count"] == 0


@pytest.mark.asyncio
async def test_empty_range_hour(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "hour",
            "since": int(_BASE_TS + _DAY * 100),
            "until": int(_BASE_TS + _DAY * 101),
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["buckets"] == []
    assert data["totals"]["estimated_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Default since/until when omitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_since_until_applied(app_and_db):
    app, db, _ = app_and_db
    # Insert a turn at now-10 min (within default 24h window)
    await _seed_turn(db, "s1", 1, input_tokens=100, ts=time.time() - 600)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={"group_by": "session"})

    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 1


# ---------------------------------------------------------------------------
# Minion enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minion_enrichment(app_and_db):
    app, db, sm = app_and_db
    await _seed_turn(db, "minion-1", 1, input_tokens=500, ts=_BASE_TS)

    minion_info = MagicMock()
    minion_info.name = "Worker A"
    minion_info.is_minion = True
    minion_info.parent_overseer_id = "overseer-1"

    sm.get_session_info = AsyncMock(return_value=minion_info)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["is_minion"] is True
    assert row["parent_session_id"] == "overseer-1"
    assert row["session_name"] == "Worker A"


@pytest.mark.asyncio
async def test_deleted_session_shows_as_deleted(app_and_db):
    app, db, sm = app_and_db
    await _seed_turn(db, "old-sess", 1, input_tokens=100, ts=_BASE_TS)

    sm.get_session_info = AsyncMock(return_value=None)  # session not in memory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["session_name"] == "(deleted)"


# ---------------------------------------------------------------------------
# totals.top_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_session_name_sourced_from_deleted_at_not_session_manager(app_and_db):
    """Issue #1941: a session flagged deleted_at must show as "(deleted)" from
    its own row field, even if SessionManager still (implausibly) has info for
    it — deleted_at is the single source of truth, not session-manager absence."""
    app, db, sm = app_and_db
    await _seed_turn(db, "flagged-sess", 1, input_tokens=100, ts=_BASE_TS)
    await _mark_deleted(db, "flagged-sess")

    still_present_info = MagicMock()
    still_present_info.name = "Should Not Show"
    sm.get_session_info = AsyncMock(return_value=still_present_info)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["session_name"] == "(deleted)"
    sm.get_session_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_include_deleted_false_excludes_flagged_session(app_and_db):
    app, db, _ = app_and_db
    await _seed_turn(db, "active-sess", 1, input_tokens=100, ts=_BASE_TS)
    await _seed_turn(db, "deleted-sess", 1, input_tokens=200, ts=_BASE_TS)
    await _mark_deleted(db, "deleted-sess")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Default (omitted) includes deleted sessions.
        default_resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })
        excluded_resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
            "include_deleted": "false",
        })

    default_sids = {r["session_id"] for r in default_resp.json()["rows"]}
    assert default_sids == {"active-sess", "deleted-sess"}

    excluded_sids = {r["session_id"] for r in excluded_resp.json()["rows"]}
    assert excluded_sids == {"active-sess"}


@pytest.mark.asyncio
async def test_top_session_matches_max_cost(app_and_db):
    app, db, sm = app_and_db
    await _seed_turn(db, "cheap", 1, input_tokens=100, ts=_BASE_TS)
    await _seed_turn(db, "expensive", 1, input_tokens=100000, ts=_BASE_TS)

    cheap_info = MagicMock()
    cheap_info.name = "Cheap"
    cheap_info.is_minion = False
    cheap_info.parent_overseer_id = None

    expensive_info = MagicMock()
    expensive_info.name = "Expensive"
    expensive_info.is_minion = False
    expensive_info.parent_overseer_id = None

    async def _get_session_info(sid):
        return expensive_info if sid == "expensive" else cheap_info

    sm.get_session_info = AsyncMock(side_effect=_get_session_info)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/analytics/usage", params={
            "group_by": "session",
            "since": int(_BASE_TS - 1),
            "until": int(_BASE_TS + _DAY),
        })

    data = resp.json()
    assert data["totals"]["top_session"]["session_id"] == "expensive"
