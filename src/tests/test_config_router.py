"""Tests for src/routers/config.py — split ownership / merged read (issue #498).

Uses a stub Backend (AsyncMock on webui.backend_client) rather than a real
Backend process — fast, and this router's own logic (which keys go where) is
what's under test, not Backend's config validation (covered separately in
backend/tests/test_config_router.py).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app(tmp_path, backend_config: dict | None = None):
    from src.routers.config import build_router

    config_file = tmp_path / "config.json"
    config_file.write_text("{}")

    webui = MagicMock()
    webui.config_file = config_file
    webui.backend_client = MagicMock()
    webui.backend_client.get_json = AsyncMock(
        return_value={"config": backend_config or {"features": {"skill_sync_enabled": True}}}
    )
    webui.backend_client.request_json = AsyncMock(
        return_value={"config": backend_config or {"features": {"skill_sync_enabled": True}}}
    )

    app = FastAPI()
    app.include_router(build_router(webui))
    return app, config_file, webui


@pytest.mark.asyncio
async def test_get_config_merges_frontend_and_backend_sections(tmp_path):
    app, _, webui = _make_app(tmp_path, backend_config={"features": {"skill_sync_enabled": True}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")

    assert resp.status_code == 200
    body = resp.json()["config"]
    assert body["features"]["skill_sync_enabled"] is True  # from Backend
    assert "networking" in body  # from local Frontend read
    assert "backend_connection" in body
    webui.backend_client.get_json.assert_awaited_once_with("/api/config")


@pytest.mark.asyncio
async def test_put_config_networking_only_does_not_call_backend(tmp_path):
    app, config_file, webui = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "networking": {"allow_network_binding": True, "acknowledged_risk": True},
        })

    assert resp.status_code == 200
    assert resp.json()["config"]["networking"]["allow_network_binding"] is True
    webui.backend_client.request_json.assert_not_awaited()

    on_disk = json.loads(config_file.read_text())
    assert on_disk["networking"]["allow_network_binding"] is True


@pytest.mark.asyncio
async def test_put_config_backend_only_does_not_touch_local_file(tmp_path):
    app, config_file, webui = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"skill_sync_enabled": False},
        })

    assert resp.status_code == 200
    webui.backend_client.request_json.assert_awaited_once_with(
        "PUT", "/api/config", json={"features": {"skill_sync_enabled": False}}
    )
    on_disk = json.loads(config_file.read_text())
    assert "networking" not in on_disk  # untouched — this PUT had no frontend-owned keys


@pytest.mark.asyncio
async def test_put_config_mixed_body_splits_between_both(tmp_path):
    app, config_file, webui = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "networking": {"acknowledged_risk": True},
            "features": {"skill_sync_enabled": False},
        })

    assert resp.status_code == 200
    webui.backend_client.request_json.assert_awaited_once_with(
        "PUT", "/api/config", json={"features": {"skill_sync_enabled": False}}
    )
    on_disk = json.loads(config_file.read_text())
    assert on_disk["networking"]["acknowledged_risk"] is True


@pytest.mark.asyncio
async def test_put_config_backend_connection_is_frontend_owned(tmp_path):
    app, config_file, webui = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "backend_connection": {"remote_backend_url": "http://127.0.0.1:8100"},
        })

    assert resp.status_code == 200
    webui.backend_client.request_json.assert_not_awaited()
    on_disk = json.loads(config_file.read_text())
    assert on_disk["backend_connection"]["remote_backend_url"] == "http://127.0.0.1:8100"
