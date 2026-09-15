"""Tests for src/routers/relay.py's Backend-unreachable handling (issue #1844).

Before this fix, a connection failure from backend_client.relay() propagated
straight through handle_exceptions' generic `except Exception`, logging a
full ERROR-level traceback per request. Now relay.py pre-empts the decorator
with to_http_exception() so only the shared BackendReachabilityTracker logs
(covered separately in test_backend_reachability.py / test_backend_client.py)
— this file proves the response contract to the browser is unchanged (500,
same detail-construction) and that no unhandled exception escapes the ASGI
test client.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.routers.relay import build_router


def _make_app():
    webui = MagicMock()
    webui.backend_client.relay = AsyncMock(side_effect=httpx.ConnectError("refused"))
    app = FastAPI()
    app.include_router(build_router(webui))
    return app, webui


@pytest.mark.asyncio
async def test_issue_1844_relay_to_backend_connect_error_returns_500():
    app, _ = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "An internal error occurred"}


@pytest.mark.asyncio
async def test_issue_1844_relay_oauth_callback_connect_error_returns_500():
    app, _ = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/oauth/callback?code=abc")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "An internal error occurred"}


def _make_success_app():
    webui = MagicMock()
    webui.backend_client.relay = AsyncMock(return_value=MagicMock())
    app = FastAPI()
    app.include_router(build_router(webui))
    return app, webui


@pytest.mark.asyncio
async def test_issue_1933_halt_all_path_relayed_with_longer_timeout():
    app, webui = _make_success_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/legions/legion-1/halt-all")

    _, kwargs = webui.backend_client.relay.call_args
    assert kwargs["timeout"] == 120.0


@pytest.mark.asyncio
async def test_issue_1933_other_paths_relayed_with_default_timeout():
    app, webui = _make_success_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/sessions")

    _, kwargs = webui.backend_client.relay.call_args
    assert kwargs["timeout"] is None
