"""Tests for GET /api/config pricing_defaults and PUT /api/config removed_models (issue #1192)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

_SONNET = "claude-sonnet-4-6"
_OPUS = "claude-opus-4-7"
_HAIKU = "claude-haiku-4-5"


def _make_app(tmp_path):
    from src.routers.config import build_router

    config_file = tmp_path / "config.json"

    webui = MagicMock()
    webui.config_file = config_file
    webui.skill_manager.cleanup_symlinks = AsyncMock()
    webui.skill_manager.sync = AsyncMock()

    app = FastAPI()
    app.include_router(build_router(webui))
    return app, config_file


# ── GET /api/config ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_config_includes_pricing_defaults(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")

    assert resp.status_code == 200
    body = resp.json()
    assert "pricing_defaults" in body["config"]
    defaults = body["config"]["pricing_defaults"]
    assert _SONNET in defaults
    assert _OPUS in defaults
    assert _HAIKU in defaults


@pytest.mark.asyncio
async def test_get_config_pricing_defaults_match_default_pricing_rates(tmp_path):
    from src.config_manager import default_pricing_rates

    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")

    body = resp.json()
    defaults = body["config"]["pricing_defaults"]
    expected = {mid: r.to_dict() for mid, r in default_pricing_rates().items()}
    assert defaults == expected


# ── PUT /api/config — removed_models happy path ──────────────────────────────

@pytest.mark.asyncio
async def test_put_config_removed_models_removes_custom_entry(tmp_path):
    app, config_file = _make_app(tmp_path)

    # First add a custom model
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "pricing": {
                "rates": {
                    "custom-model-x": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
                }
            }
        })
        assert put_resp.status_code == 200
        assert "custom-model-x" in put_resp.json()["config"]["pricing"]["rates"]

        # Now remove it
        del_resp = await client.put("/api/config", json={
            "pricing": {"removed_models": ["custom-model-x"]}
        })

    assert del_resp.status_code == 200
    assert "custom-model-x" not in del_resp.json()["config"]["pricing"]["rates"]


@pytest.mark.asyncio
async def test_put_config_removed_models_ignores_nonexistent_model(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "pricing": {"removed_models": ["no-such-model"]}
        })
    assert resp.status_code == 200


# ── PUT /api/config — cannot remove default model ────────────────────────────

@pytest.mark.asyncio
async def test_put_config_cannot_remove_default_model(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "pricing": {"removed_models": [_SONNET]}
        })
    assert resp.status_code == 400
    body = resp.json()
    assert "default_model" in body.get("detail", "")


# ── PUT /api/config — removed_models validation ──────────────────────────────

@pytest.mark.asyncio
async def test_put_config_removed_models_must_be_list(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "pricing": {"removed_models": "not-a-list"}
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_config_removed_models_must_contain_strings(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "pricing": {"removed_models": [123]}
        })
    assert resp.status_code == 400


# ── PUT /api/config — negative rates still rejected ──────────────────────────

@pytest.mark.asyncio
async def test_put_config_rejects_negative_rate(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "pricing": {
                "rates": {
                    _SONNET: {"input": -1.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
                }
            }
        })
    assert resp.status_code == 400


# ── PUT /api/config — response includes pricing_defaults ─────────────────────

@pytest.mark.asyncio
async def test_put_config_response_includes_pricing_defaults(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "pricing": {"rates": {_HAIKU: {"input": 0.90, "output": 4.5, "cache_write": 1.1, "cache_read": 0.09}}}
        })
    assert resp.status_code == 200
    assert "pricing_defaults" in resp.json()["config"]
