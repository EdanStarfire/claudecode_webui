"""Tests for GET /oauth/client-metadata.json (issue #2231).

draft-ietf-oauth-client-id-metadata-document: a self-describing client
identity document hosted on Frontend only (src/routers/core.py) — fetched
directly by MCP authorization servers that don't support RFC 7591 DCR.
client_id is computed from the serving request's own URL, since that's the
one authoritative public-facing origin (mirrors how /oauth/callback is
exempted from auth — see test_oauth_callback_relay.py for that route's own
coverage).
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.routers import core
from src.web_server import DEFAULT_EXEMPT_PATHS


def _make_app():
    webui = object()  # core.build_router's handlers used here need nothing from webui
    app = FastAPI()
    app.include_router(core.build_router(webui))
    return app


def test_exempt_from_auth_middleware():
    assert "/oauth/client-metadata.json" in DEFAULT_EXEMPT_PATHS


@pytest.mark.asyncio
async def test_returns_valid_document_matching_request_origin():
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/oauth/client-metadata.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["client_id"] == "http://test/oauth/client-metadata.json"
    assert data["redirect_uris"] == ["http://test/oauth/callback"]
    assert data["token_endpoint_auth_method"] == "none"
    assert data["grant_types"] == ["authorization_code", "refresh_token"]
    assert data["response_types"] == ["code"]
    assert data["client_name"]


@pytest.mark.asyncio
async def test_reachable_without_any_authorization_header():
    """No Authorization header sent at all — the route itself never checks for
    one; it's DEFAULT_EXEMPT_PATHS membership (asserted above) that
    makes this reachable unauthenticated in the real app stack."""
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://example.com") as client:
        resp = await client.get("/oauth/client-metadata.json")

    assert resp.status_code == 200
    assert resp.json()["client_id"] == "https://example.com/oauth/client-metadata.json"
