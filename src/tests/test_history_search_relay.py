"""Issue #2030 (stage 3 PR 3B): ``/search`` rides Frontend's generic relay (query string intact,
503 ``search_busy`` / 409 / 400 verbatim) and a browser abort cancels the in-flight relay."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.routers import relay as relay_router
from src.routers import sessions as sessions_router


def _app(relay):
    webui = MagicMock()
    webui.backend_client.relay = relay
    app = FastAPI()
    app.include_router(sessions_router.build_router(webui))
    app.include_router(relay_router.build_router(webui))
    return app, webui


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/sessions/s1/search", "/api/projects/p1/archives/s1/20260101/search"],
)
async def test_search_is_relayed_with_query_string_and_no_cursor_substitution(path):
    payload = {"results": [], "next_cursor": None, "exhausted": True, "superseded": False}
    app, webui = _app(AsyncMock(return_value=JSONResponse(payload)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(path, params={"q": "needle", "cursor": "s1:h:5"})
    assert resp.status_code == 200 and resp.json() == payload
    request, relayed_path = webui.backend_client.relay.call_args.args[:2]
    assert relayed_path == path
    assert request.query_params["q"] == "needle" and request.query_params["cursor"] == "s1:h:5"
    webui.poll_relay.touch_session.assert_not_called()
    webui.poll_relay.wait_until_relayed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,detail", [(503, "search_busy"), (409, "cursor_invalid"), (400, "query_too_short")])
async def test_error_statuses_pass_through_verbatim(status, detail):
    app, _ = _app(AsyncMock(return_value=JSONResponse({"detail": detail}, status_code=status)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/sessions/s1/search", params={"q": "ne"})
    assert resp.status_code == status and resp.json() == {"detail": detail}


@pytest.mark.asyncio
async def test_browser_disconnect_cancels_the_inflight_search_relay(monkeypatch):
    monkeypatch.setattr(relay_router, "_DISCONNECT_POLL_SECONDS", 0.02)
    cancelled = asyncio.Event()

    async def slow_relay(request, path, timeout=None):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    class GoneRequest:
        method = "GET"

        async def is_disconnected(self):
            return True

    response = await relay_router._relay_until_disconnect(GoneRequest(), slow_relay(None, "x"))
    assert response.status_code == 499
    await asyncio.wait_for(cancelled.wait(), 1)


@pytest.mark.asyncio
async def test_non_search_paths_are_not_polled():
    assert relay_router._is_search_path("sessions/s1/search")
    assert relay_router._is_search_path("projects/p/archives/s/a/search")
    assert not relay_router._is_search_path("sessions/s1/messages")
    assert not relay_router._is_search_path("mcp-configs/search")
