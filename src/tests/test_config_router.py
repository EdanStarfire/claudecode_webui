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


@pytest.mark.skip(
    reason="Issue #498: pricing_defaults now come from Backend's PricingConfig "
    "(config.py's inline `from ..config_manager import` is broken pending Phase 2's "
    "merged-read rewrite) — comparing against backend.config_manager here would also "
    "violate the src/ import boundary (src/tests/test_import_boundary.py). Restore "
    "once Phase 2 wires config.py's merged read against a real/stubbed Backend."
)
@pytest.mark.asyncio
async def test_get_config_pricing_defaults_match_default_pricing_rates(tmp_path):
    pass


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


# ── GET/PUT /api/config — max_subagents_per_session (issue #1670) ────────────

@pytest.mark.asyncio
async def test_get_config_max_subagents_default(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["max_subagents_per_session"] == 200


@pytest.mark.asyncio
async def test_put_config_max_subagents_round_trip(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "features": {"max_subagents_per_session": 50}
        })
        assert put_resp.status_code == 200
        assert put_resp.json()["config"]["features"]["max_subagents_per_session"] == 50

        get_resp = await client.get("/api/config")
    assert get_resp.json()["config"]["features"]["max_subagents_per_session"] == 50


@pytest.mark.asyncio
async def test_put_config_max_subagents_accepts_upper_bound_200(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"max_subagents_per_session": 200}
        })
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["max_subagents_per_session"] == 200


@pytest.mark.asyncio
async def test_put_config_max_subagents_rejects_zero(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"max_subagents_per_session": 0}
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_config_max_subagents_rejects_above_ceiling(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"max_subagents_per_session": 201}
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_config_max_subagents_rejects_non_int(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"max_subagents_per_session": "fifty"}
        })
    assert resp.status_code == 400


# ── GET/PUT /api/config — forward_subagent_text (issue #1671) ────────────────

@pytest.mark.asyncio
async def test_get_config_forward_subagent_text_default(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["forward_subagent_text"] is True


@pytest.mark.asyncio
async def test_put_config_forward_subagent_text_round_trip(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "features": {"forward_subagent_text": False}
        })
        assert put_resp.status_code == 200
        assert put_resp.json()["config"]["features"]["forward_subagent_text"] is False

        get_resp = await client.get("/api/config")
    assert get_resp.json()["config"]["features"]["forward_subagent_text"] is False


@pytest.mark.asyncio
async def test_put_config_forward_subagent_text_rejects_non_bool(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"forward_subagent_text": "yes"}
        })
    assert resp.status_code == 400


# ── GET/PUT /api/config — allow_background_agent (issue #1688) ──────────────

@pytest.mark.asyncio
async def test_get_config_allow_background_agent_default(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["allow_background_agent"] is False


@pytest.mark.asyncio
async def test_put_config_allow_background_agent_round_trip(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "features": {"allow_background_agent": True}
        })
        assert put_resp.status_code == 200
        assert put_resp.json()["config"]["features"]["allow_background_agent"] is True

        get_resp = await client.get("/api/config")
    assert get_resp.json()["config"]["features"]["allow_background_agent"] is True


@pytest.mark.asyncio
async def test_put_config_allow_background_agent_rejects_non_bool(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"allow_background_agent": "yes"}
        })
    assert resp.status_code == 400


# ── GET/PUT /api/config — resume_batch_size (issue #1733) ───────────────────

@pytest.mark.asyncio
async def test_get_config_resume_batch_size_default(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["resume_batch_size"] == 10


@pytest.mark.asyncio
async def test_put_config_resume_batch_size_round_trip(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "features": {"resume_batch_size": 25}
        })
        assert put_resp.status_code == 200
        assert put_resp.json()["config"]["features"]["resume_batch_size"] == 25

        get_resp = await client.get("/api/config")
    assert get_resp.json()["config"]["features"]["resume_batch_size"] == 25


@pytest.mark.asyncio
async def test_put_config_resume_batch_size_accepts_one(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_size": 1}
        })
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["resume_batch_size"] == 1


@pytest.mark.asyncio
async def test_put_config_resume_batch_size_rejects_zero(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_size": 0}
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_config_resume_batch_size_rejects_negative(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_size": -5}
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_config_resume_batch_size_rejects_non_int(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_size": "ten"}
        })
    assert resp.status_code == 400


# ── GET/PUT /api/config — resume_batch_delay_seconds (issue #1791) ──────────

@pytest.mark.asyncio
async def test_get_config_resume_batch_delay_seconds_default(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["resume_batch_delay_seconds"] == 5


@pytest.mark.asyncio
async def test_put_config_resume_batch_delay_seconds_round_trip(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "features": {"resume_batch_delay_seconds": 12}
        })
        assert put_resp.status_code == 200
        assert put_resp.json()["config"]["features"]["resume_batch_delay_seconds"] == 12

        get_resp = await client.get("/api/config")
    assert get_resp.json()["config"]["features"]["resume_batch_delay_seconds"] == 12


@pytest.mark.asyncio
async def test_put_config_resume_batch_delay_seconds_accepts_zero(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_delay_seconds": 0}
        })
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["resume_batch_delay_seconds"] == 0


@pytest.mark.asyncio
async def test_put_config_resume_batch_delay_seconds_rejects_negative(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_delay_seconds": -5}
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_config_resume_batch_delay_seconds_rejects_non_int(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"resume_batch_delay_seconds": "five"}
        })
    assert resp.status_code == 400


# ── GET/PUT /api/config — enable_experimental_nav_header (issue #1723) ──────

@pytest.mark.asyncio
async def test_get_config_enable_experimental_nav_header_default(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["features"]["enable_experimental_nav_header"] is False


@pytest.mark.asyncio
async def test_put_config_enable_experimental_nav_header_round_trip(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put_resp = await client.put("/api/config", json={
            "features": {"enable_experimental_nav_header": True}
        })
        assert put_resp.status_code == 200
        assert put_resp.json()["config"]["features"]["enable_experimental_nav_header"] is True

        get_resp = await client.get("/api/config")
    assert get_resp.json()["config"]["features"]["enable_experimental_nav_header"] is True


@pytest.mark.asyncio
async def test_put_config_enable_experimental_nav_header_rejects_non_bool(tmp_path):
    app, _ = _make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put("/api/config", json={
            "features": {"enable_experimental_nav_header": "yes"}
        })
    assert resp.status_code == 400
