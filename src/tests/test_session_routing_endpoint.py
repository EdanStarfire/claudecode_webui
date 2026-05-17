"""
Tests for GET /api/sessions/{session_id}/routing endpoint (issue #1427 Phase 3).

Covers:
  - 401 when no Bearer header
  - 401 when wrong Bearer token
  - 200 with empty rewrites when no catalog is selected
  - 200 with populated rewrites and virtual_key when catalog selected
  - Hostname rewrite map uses proxy_mgr.port
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


def _make_session(token: str = "valid-token") -> MagicMock:
    session = MagicMock()
    session.secret_fetch_token = token
    return session


def _make_webui(
    session: MagicMock | None = None,
    proxy_mgr: MagicMock | None = None,
) -> MagicMock:
    webui = MagicMock()
    webui.coordinator.session_manager.get_session_info = AsyncMock(
        return_value=session
    )
    webui.litellm_proxy_manager = proxy_mgr
    return webui


def _make_proxy_mgr(port: int = 4000, routing: dict | None = None) -> MagicMock:
    mgr = MagicMock()
    mgr.port = port
    mgr.get_session_routing.return_value = routing
    mgr.build_hostname_rewrites.return_value = {
        "api.anthropic.com": f"cc-webui.internal:{port}"
    }
    return mgr


@pytest.fixture
def session_id() -> str:
    return "test-session-1427"


@pytest.fixture
def valid_token() -> str:
    return "correct-session-token"


async def _client(webui) -> AsyncClient:
    from fastapi import FastAPI

    from src.routers.session_routing import build_router

    app = FastAPI()
    app.include_router(build_router(webui))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_bearer_returns_401(session_id, valid_token):
    session = _make_session(token=valid_token)
    webui = _make_webui(session=session)

    async with await _client(webui) as client:
        resp = await client.get(f"/api/sessions/{session_id}/routing")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_bearer_returns_401(session_id, valid_token):
    session = _make_session(token=valid_token)
    webui = _make_webui(session=session)

    async with await _client(webui) as client:
        resp = await client.get(
            f"/api/sessions/{session_id}/routing",
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_session_returns_401(session_id):
    webui = _make_webui(session=None)

    async with await _client(webui) as client:
        resp = await client.get(
            f"/api/sessions/{session_id}/routing",
            headers={"Authorization": "Bearer any-token"},
        )

    assert resp.status_code in (401, 404)


# ---------------------------------------------------------------------------
# No catalog selected — returns empty rewrites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_catalog_returns_empty_rewrites(session_id, valid_token):
    session = _make_session(token=valid_token)
    proxy_mgr = _make_proxy_mgr(routing=None)
    webui = _make_webui(session=session, proxy_mgr=proxy_mgr)

    async with await _client(webui) as client:
        resp = await client.get(
            f"/api/sessions/{session_id}/routing",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["hostname_rewrites"] == {}
    assert body["virtual_key"] is None
    assert body["model_map"] == {}
    assert body["default_model"] is None


# ---------------------------------------------------------------------------
# Catalog selected — returns populated rewrites and virtual_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_selected_returns_rewrites_and_vkey(session_id, valid_token):
    session = _make_session(token=valid_token)
    routing = {
        "virtual_key": "lc-abc123",
        "base_url": "http://127.0.0.1:4000/",
        "model_map": {},
        "default_model": "bedrock--claude-sonnet",
    }
    proxy_mgr = _make_proxy_mgr(port=4000, routing=routing)
    webui = _make_webui(session=session, proxy_mgr=proxy_mgr)

    async with await _client(webui) as client:
        resp = await client.get(
            f"/api/sessions/{session_id}/routing",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["virtual_key"] == "lc-abc123"
    assert "api.anthropic.com" in body["hostname_rewrites"]
    assert "cc-webui.internal:4000" in body["hostname_rewrites"]["api.anthropic.com"]
    assert body["model_map"] == {}
    assert body["default_model"] == "bedrock--claude-sonnet"


@pytest.mark.asyncio
async def test_tier_breakout_returns_model_map(session_id, valid_token):
    """When tier breakout is active, response includes populated model_map."""
    session = _make_session(token=valid_token)
    tier_map = {
        "haiku": "bedrock--haiku",
        "sonnet": "bedrock--sonnet",
        "opus": "bedrock--opus",
        "default": "bedrock--sonnet",
    }
    routing = {
        "virtual_key": "lc-tier-key",
        "base_url": "http://127.0.0.1:4000/",
        "model_map": tier_map,
        "default_model": "bedrock--sonnet",
    }
    proxy_mgr = _make_proxy_mgr(port=4000, routing=routing)
    webui = _make_webui(session=session, proxy_mgr=proxy_mgr)

    async with await _client(webui) as client:
        resp = await client.get(
            f"/api/sessions/{session_id}/routing",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_map"] == tier_map
    assert body["default_model"] == "bedrock--sonnet"


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [4000, 4001, 9000])
async def test_rewrite_map_uses_proxy_port(session_id, valid_token, port):
    session = _make_session(token=valid_token)
    routing = {
        "virtual_key": "lc-xyz",
        "base_url": f"http://127.0.0.1:{port}/",
        "model_map": {},
        "default_model": None,
    }
    proxy_mgr = _make_proxy_mgr(port=port, routing=routing)
    webui = _make_webui(session=session, proxy_mgr=proxy_mgr)

    async with await _client(webui) as client:
        resp = await client.get(
            f"/api/sessions/{session_id}/routing",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert f"cc-webui.internal:{port}" in body["hostname_rewrites"]["api.anthropic.com"]
