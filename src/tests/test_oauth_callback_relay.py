"""Tests for src/web_server.py's dynamic OAuth callback path mirroring (issue #498).

Backend is typically 127.0.0.1-only and not independently reachable by an
external OAuth provider — only Frontend has a public bind address in the
common case, so a custom (non-default) OAuth callback path can only ever
complete by being relayed through Frontend. This covers the mirroring logic
(resync_oauth_callback_paths) that makes that work without Frontend needing
advance knowledge of Backend's dynamically-registered paths.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.web_server import ClaudeWebUI


def _make_webui():
    webui = ClaudeWebUI.__new__(ClaudeWebUI)  # bypass __init__ (needs frontend/dist)
    from fastapi import FastAPI
    webui.app = FastAPI()
    webui._oauth_callback_paths = set()
    webui.backend_client = MagicMock()
    return webui


def test_add_and_remove_oauth_callback_relay_route():
    webui = _make_webui()

    webui._add_oauth_callback_relay_route("/custom/callback")
    assert "/custom/callback" in webui._oauth_callback_paths
    paths = [getattr(r, "path", None) for r in webui.app.router.routes]
    assert "/custom/callback" in paths

    webui._remove_oauth_callback_relay_route("/custom/callback")
    assert "/custom/callback" not in webui._oauth_callback_paths
    paths = [getattr(r, "path", None) for r in webui.app.router.routes]
    assert "/custom/callback" not in paths


def test_add_oauth_callback_route_exempts_from_auth_middleware():
    from src.web_server import AuthMiddleware
    webui = _make_webui()

    webui._add_oauth_callback_relay_route("/another/callback")
    try:
        assert "/another/callback" in AuthMiddleware.EXEMPT_PATHS
    finally:
        webui._remove_oauth_callback_relay_route("/another/callback")
        assert "/another/callback" not in AuthMiddleware.EXEMPT_PATHS


@pytest.mark.asyncio
async def test_resync_adds_new_paths_from_backend_registry():
    webui = _make_webui()
    webui.backend_client.get_json = AsyncMock(
        return_value={"paths": ["/oauth/callback", "/custom/callback-a", "/custom/callback-b"]}
    )

    await webui.resync_oauth_callback_paths()

    # /oauth/callback is excluded — it's statically handled by relay.py already.
    assert webui._oauth_callback_paths == {"/custom/callback-a", "/custom/callback-b"}


@pytest.mark.asyncio
async def test_resync_removes_stale_paths_no_longer_on_backend():
    webui = _make_webui()
    webui.backend_client.get_json = AsyncMock(return_value={"paths": ["/custom/callback-a"]})
    await webui.resync_oauth_callback_paths()
    assert webui._oauth_callback_paths == {"/custom/callback-a"}

    webui.backend_client.get_json = AsyncMock(return_value={"paths": []})
    await webui.resync_oauth_callback_paths()
    assert webui._oauth_callback_paths == set()
    paths = [getattr(r, "path", None) for r in webui.app.router.routes]
    assert "/custom/callback-a" not in paths


@pytest.mark.asyncio
async def test_resync_is_a_noop_on_backend_error():
    import httpx

    webui = _make_webui()
    webui._oauth_callback_paths = {"/custom/existing"}
    webui.backend_client.get_json = AsyncMock(side_effect=httpx.ConnectError("refused"))

    await webui.resync_oauth_callback_paths()

    assert webui._oauth_callback_paths == {"/custom/existing"}  # untouched, no crash


@pytest.mark.asyncio
async def test_dynamic_callback_route_actually_relays():
    webui = _make_webui()
    webui.backend_client.relay = AsyncMock()
    from fastapi.responses import JSONResponse
    webui.backend_client.relay.return_value = JSONResponse({"ok": True})

    webui._add_oauth_callback_relay_route("/custom/callback")

    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        resp = await client.get("/custom/callback?code=abc123")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    webui.backend_client.relay.assert_awaited_once()
    call_args = webui.backend_client.relay.call_args
    assert call_args[0][1] == "/custom/callback"
