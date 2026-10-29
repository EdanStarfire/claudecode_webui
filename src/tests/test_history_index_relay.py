"""Issue #2030 (stage 2 PR A): /history_index rides Frontend's generic relay like ``before=``
pages — no touch_session / wait_until_relayed, 503 ``index_building`` passes through."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.routers import relay as relay_router
from src.routers import sessions as sessions_router


def _app(response):
    webui = MagicMock()
    webui.backend_client.relay = AsyncMock(return_value=response)
    app = FastAPI()
    # same relative order as src/routers/__init__.py: dedicated routes first, relay last
    app.include_router(sessions_router.build_router(webui))
    app.include_router(relay_router.build_router(webui))
    return app, webui


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/sessions/s1/history_index",
        "/api/projects/p1/archives/s1/20260101/history_index",
    ],
)
async def test_history_index_is_relayed_without_cursor_substitution(path):
    payload = {"history_id": "h", "total_bytes": 0, "turns": [], "compactions": []}
    app, webui = _app(JSONResponse(payload))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get(path)
    assert resp.status_code == 200 and resp.json() == payload
    assert webui.backend_client.relay.call_args.args[1] == path
    webui.poll_relay.touch_session.assert_not_called()
    webui.poll_relay.wait_until_relayed.assert_not_called()
    webui.backend_client.get_json.assert_not_called()  # not the /messages dedicated route


@pytest.mark.asyncio
async def test_index_building_503_passes_through_verbatim():
    app, _ = _app(JSONResponse({"detail": "index_building"}, status_code=503))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/sessions/s1/history_index")
    assert resp.status_code == 503 and resp.json() == {"detail": "index_building"}


@pytest.mark.asyncio
async def test_cursor_invalid_409_passes_through_verbatim():
    app, _ = _app(JSONResponse({"detail": "cursor_invalid"}, status_code=409))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.get("/api/sessions/s1/history_index")
    assert resp.status_code == 409 and resp.json() == {"detail": "cursor_invalid"}
