"""Tests for src/routers/poll.py's `backend_status` field (issue #1989).

Before this fix, a Backend-side outage was invisible to the poll channel: the
poll-relay background task just backs off silently, so the local EventQueue's
long-poll simply times out with zero new events and returns a normal
`200 {"events": [], ...}` — the browser had no way to learn Backend was down
without a REST call actively failing first. This asserts the multiplexed poll
endpoint carries a `backend_status` field ("ok" / "unreachable" / "degraded")
on every response, using a real shared.event_queue.EventQueue (not a mock) so
wait_for_events()/events_since() behave like production.

Epic #1990 stage 5c phase 5: the legacy single-stream GET /api/poll/ui and
GET /api/poll/session/{id} endpoints these tests originally covered are
removed — superseded by the POST /api/poll tests below (added in phase 2,
now the only coverage for this field).
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.event_queue import EventQueue
from src.routers.poll import build_router


def _make_webui(*, is_unreachable=False, degraded=None):
    webui = MagicMock()
    webui.ui_queue = EventQueue()
    webui.session_queues = {}
    webui.poll_relay.start_ui_relay = MagicMock()
    webui.poll_relay.touch_session = MagicMock()
    webui.backend_client.reachability.is_unreachable = is_unreachable
    # MagicMock()'s auto-created attributes are truthy, so backend_supervisor must be
    # set explicitly — see test_relay_router.py's _make_app for the same gotcha.
    if degraded is None:
        webui.backend_supervisor = None
    else:
        webui.backend_supervisor.degraded = degraded
    app = FastAPI()
    app.include_router(build_router(webui))
    return app, webui


@pytest.mark.asyncio
async def test_poll_multiplex_backend_status_ok():
    app, _ = _make_webui(is_unreachable=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/poll", json={"timeout": 0, "ui": {"since": 0}})

    assert resp.status_code == 200
    assert resp.json()["backend_status"] == "ok"


@pytest.mark.asyncio
async def test_poll_multiplex_backend_status_unreachable():
    app, webui = _make_webui(is_unreachable=True)
    webui.session_queues["session-1"] = EventQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/poll",
            json={"timeout": 0, "ui": {"since": 0}, "sessions": {"session-1": {"since": 0}}},
        )

    assert resp.status_code == 200
    assert resp.json()["backend_status"] == "unreachable"


@pytest.mark.asyncio
async def test_poll_multiplex_backend_status_degraded_takes_precedence():
    app, webui = _make_webui(is_unreachable=True, degraded=True)
    webui.session_queues["session-1"] = EventQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/poll",
            json={"timeout": 0, "ui": {"since": 0}, "sessions": {"session-1": {"since": 0}}},
        )

    assert resp.status_code == 200
    assert resp.json()["backend_status"] == "degraded"


@pytest.mark.asyncio
async def test_poll_multiplex_backend_status_supervisor_not_degraded():
    app, _ = _make_webui(is_unreachable=False, degraded=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/poll", json={"timeout": 0, "ui": {"since": 0}})

    assert resp.status_code == 200
    assert resp.json()["backend_status"] == "ok"


@pytest.mark.asyncio
async def test_poll_multiplex_session_only_backend_status_ok():
    app, webui = _make_webui(is_unreachable=False)
    webui.session_queues["session-1"] = EventQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/poll", json={"timeout": 0, "sessions": {"session-1": {"since": 0}}}
        )

    assert resp.status_code == 200
    assert resp.json()["backend_status"] == "ok"
