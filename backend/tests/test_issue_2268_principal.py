"""End-to-end tests for issue #2268 AC3 — Principal set per request by AuthMiddleware.

Covers the real request path (not just the unit-level ApplicationService.
current_principal() tests in test_application_service.py): a request through the
full Starlette middleware stack must see the Principal AuthMiddleware.dispatch()
set, read back via ApplicationService.current_principal() from inside a route
handler running in the request's own task.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.web_server import BackendApp


@pytest.fixture
async def auth_env(tmp_path):
    """A BackendApp instance with auth ENABLED, to verify Principal wiring for real."""
    data_dir = tmp_path / "test_data_2268_principal"
    data_dir.mkdir()
    config_file = tmp_path / "config.json"
    webui = BackendApp(
        data_dir=data_dir,
        config_file=config_file,
        auth_token="test-token-2268",
        host="127.0.0.1",
        port=8000,
    )
    await webui.initialize()

    @webui.app.get("/__test/current_principal")
    async def _current_principal():
        principal = webui.service.current_principal()
        return {"principal": None if principal is None else vars(principal)}

    transport = ASGITransport(app=webui.app)
    client = AsyncClient(transport=transport, base_url="http://testserver")

    yield webui, client, {"Authorization": "Bearer test-token-2268"}

    await client.aclose()
    try:
        await webui.cleanup()
    except Exception:
        pass


async def test_authenticated_request_sees_a_principal(auth_env):
    webui, client, auth_headers = auth_env
    webui.coordinator.oauth_manager.complete_flow = AsyncMock()

    resp = await client.get("/__test/current_principal", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["principal"] == {
        "id": "local",
        "backend_id": webui.tenant_context.backend_id,
    }


async def test_unauthenticated_request_sees_no_principal_and_is_rejected(auth_env):
    """An unauthenticated request is rejected by AuthMiddleware before the route
    handler runs at all — the Principal contextvar is never touched for it."""
    webui, client, _ = auth_env

    resp = await client.get("/__test/current_principal")
    assert resp.status_code == 401


async def test_exempt_path_sets_no_principal(auth_env):
    """An exempt path never reaches token validation at all, so it must never
    set a Principal — distinct from the 401 case above (rejected token) and the
    success case (valid token)."""
    webui, client, _ = auth_env
    webui.auth_middleware.exempt_paths.add("/__test/current_principal")

    resp = await client.get("/__test/current_principal")
    assert resp.status_code == 200
    assert resp.json()["principal"] is None


# ---------------------------------------------------------------------------
# Concurrency isolation of the underlying ContextVar mechanism
# ---------------------------------------------------------------------------
#
# Today's real AuthMiddleware always sets the same singleton Principal, so a
# leak between concurrent requests wouldn't be observable through it yet. This
# exercises the general mechanism directly (a BaseHTTPMiddleware setting a
# *varying* Principal before call_next(), same as AuthMiddleware.dispatch()
# does) — the regression guard for the empirical claim in principal.py's
# docstring: a per-request ContextVar.set() survives call_next()'s task
# boundary without leaking into a concurrently in-flight request's task.


async def test_concurrent_requests_do_not_leak_each_others_principal():
    from fastapi import FastAPI, Request
    from starlette.middleware.base import BaseHTTPMiddleware

    from backend.principal import Principal, get_current_principal, set_current_principal

    class _VaryingPrincipalMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            set_current_principal(Principal(id=request.query_params["id"], backend_id="local"))
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(_VaryingPrincipalMiddleware)

    @app.get("/whoami")
    async def whoami():
        # A deliberate yield point between the middleware's set() and this
        # handler's read — if the two requests' tasks shared a context, one
        # request resuming after the other's set() would see the wrong value.
        await asyncio.sleep(0.01)
        return {"id": get_current_principal().id}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp_a, resp_b = await asyncio.gather(
            client.get("/whoami", params={"id": "request-a"}),
            client.get("/whoami", params={"id": "request-b"}),
        )

    assert resp_a.json()["id"] == "request-a"
    assert resp_b.json()["id"] == "request-b"
