"""Tests for src/routers/relay.py's Backend-unreachable handling (issues #1844, #1989).

Before #1844's fix, a connection failure from backend_client.relay() propagated
straight through handle_exceptions' generic `except Exception`, logging a
full ERROR-level traceback per request. relay.py pre-empts the decorator with
to_http_exception() so only the shared BackendReachabilityTracker logs
(covered separately in test_backend_reachability.py / test_backend_client.py).
Issue #1989: the response is now 502/`backend_unreachable` or 503/`backend_degraded`
instead of a flat 500, so the browser can distinguish a Backend outage from a
genuine Frontend bug — this file proves that response contract and that no
unhandled exception escapes the ASGI test client.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.backend_reachability import BackendUnreachableError
from src.routers.relay import build_router

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_FLEET_ROUTER = REPO_ROOT / "backend" / "routers" / "fleet.py"


def _extract_real_halt_all_route_path() -> str:
    """Statically extract the real halt-all route path from backend/routers/fleet.py.

    Reads and AST-parses the file as text rather than importing backend/ — src/
    must never import backend/ (see src/tests/test_import_boundary.py), so this
    is the only way to tie a src/ test to the real backend route definition.
    Ties relay.py's `full_path.endswith("/halt-all")` string match to the actual
    route: if the backend route is ever renamed or moved, this extraction fails
    (or returns a path that no longer matches), failing the test below instead
    of silently losing the 120s timeout extension with no signal (issue #1933
    review finding).
    """
    tree = ast.parse(BACKEND_FLEET_ROUTER.read_text(), filename=str(BACKEND_FLEET_ROUTER))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "post"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.endswith("/halt-all")
        ):
            return node.args[0].value
    raise AssertionError(
        f"No @router.post(...) route ending in '/halt-all' found in {BACKEND_FLEET_ROUTER} — "
        "has the halt-all route been renamed or moved?"
    )


def _make_app(*, degraded=False):
    webui = MagicMock()
    webui.backend_client.relay = AsyncMock(side_effect=httpx.ConnectError("refused"))
    # MagicMock()'s auto-created attributes are truthy, so backend_supervisor must be
    # set explicitly — otherwise `webui.backend_supervisor is not None` is always True
    # and the degraded branch silently activates regardless of intent (issue #1989).
    if degraded:
        webui.backend_supervisor.degraded = True
    else:
        webui.backend_supervisor = None
    app = FastAPI()
    app.include_router(build_router(webui))

    # Mirrors src/web_server.py's registration — a bare FastAPI() app doesn't get it
    # for free, and without it BackendUnreachableError falls back to FastAPI's default
    # HTTPException handler, which drops the sibling error_code field.
    @app.exception_handler(BackendUnreachableError)
    async def _backend_unreachable_handler(request: Request, exc: BackendUnreachableError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "error_code": exc.error_code},
        )

    return app, webui


@pytest.mark.asyncio
async def test_issue_1844_relay_to_backend_connect_error_returns_502():
    app, _ = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 502
    assert resp.json() == {"detail": "Backend is unreachable", "error_code": "backend_unreachable"}


@pytest.mark.asyncio
async def test_issue_1844_relay_oauth_callback_connect_error_returns_502():
    app, _ = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/oauth/callback?code=abc")

    assert resp.status_code == 502
    assert resp.json() == {"detail": "Backend is unreachable", "error_code": "backend_unreachable"}


@pytest.mark.asyncio
async def test_issue_1989_relay_to_backend_degraded_returns_503():
    app, _ = _make_app(degraded=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 503
    assert resp.json() == {
        "detail": "Backend is degraded and unavailable",
        "error_code": "backend_degraded",
    }


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
async def test_issue_1933_halt_all_timeout_tied_to_real_backend_route():
    """Regression guard: derive the request path from the actual backend route
    definition (not a hardcoded duplicate string) and confirm it still gets the
    120s override. A future rename of backend/routers/fleet.py's halt-all route
    fails this test (via _extract_real_halt_all_route_path's AssertionError, or
    via a timeout-kwarg mismatch below) instead of silently losing the timeout
    extension."""
    real_route_path = _extract_real_halt_all_route_path()
    request_path = real_route_path.replace("{legion_id}", "legion-1")

    app, webui = _make_success_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(request_path)

    _, kwargs = webui.backend_client.relay.call_args
    assert kwargs["timeout"] == 120.0


@pytest.mark.asyncio
async def test_issue_1933_other_paths_relayed_with_default_timeout():
    app, webui = _make_success_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/sessions")

    _, kwargs = webui.backend_client.relay.call_args
    assert kwargs["timeout"] is None
