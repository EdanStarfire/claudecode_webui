"""
Tests for /api/provider-catalog/* endpoints (issue #1427 Phase 4).

Covers:
  - GET  /api/provider-catalog        — list entries + pending_changes flag
  - POST /api/provider-catalog        — create (201) and 400 on ValueError
  - PUT  /api/provider-catalog/{id}   — update (200), 404 on KeyError, path id wins
  - DELETE /api/provider-catalog/{id} — delete (200), 404 on KeyError
  - POST /api/provider-catalog/restart — invokes rebuild + clear_pending_changes; 500 on failure
  - GET  /api/provider-catalog/status  — all six fields; last_restart ISO / null
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTRY = {
    "id": "prov-a",
    "provider_type": "anthropic",
    "litellm_params_template": {"api_key": "${secret:key}"},
    "models": [{"id": "m1", "litellm_model": "anthropic/claude-sonnet-4-6"}],
}

_ENTRY_BODY = {
    "id": "prov-a",
    "provider_type": "anthropic",
    "litellm_params_template": {"api_key": "${secret:key}"},
    "models": [{"id": "m1", "litellm_model": "anthropic/claude-sonnet-4-6"}],
}


def _make_proxy_mgr(
    is_running: bool = True,
    port: int = 4000,
    model_count: int = 0,
    last_restart: datetime | None = None,
    last_error: str | None = None,
) -> MagicMock:
    mgr = MagicMock()
    mgr.is_running = is_running
    mgr.port = port
    mgr.model_count = model_count
    mgr.last_restart = last_restart
    mgr.last_error = last_error
    mgr.rebuild = AsyncMock()
    return mgr


def _make_catalog_mgr(
    entries: list | None = None,
    pending_changes: bool = False,
) -> MagicMock:
    mgr = AsyncMock()
    mgr.list_entries.return_value = entries or []
    mgr.create_entry.return_value = _ENTRY
    mgr.update_entry.return_value = _ENTRY
    mgr.delete_entry.return_value = None
    mgr.clear_pending_changes.return_value = None
    mgr.pending_changes = pending_changes
    return mgr


def _make_webui(
    proxy_mgr: MagicMock | None = None,
    catalog_mgr: MagicMock | None = None,
    pending_changes: bool = False,
) -> MagicMock:
    webui = MagicMock()
    webui.litellm_proxy_manager = proxy_mgr or _make_proxy_mgr()
    webui.provider_catalog_manager = catalog_mgr or _make_catalog_mgr(pending_changes=pending_changes)
    return webui


async def _client(webui) -> AsyncClient:
    from fastapi import FastAPI

    from src.routers.provider_catalog import build_router

    app = FastAPI()
    app.include_router(build_router(webui))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# GET /api/provider-catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_entries_returns_entries_and_pending_flag():
    entries = [_ENTRY]
    catalog = _make_catalog_mgr(entries=entries, pending_changes=True)
    webui = _make_webui(catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.get("/api/provider-catalog")

    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == entries
    assert body["pending_changes"] is True


@pytest.mark.asyncio
async def test_list_entries_empty_catalog():
    webui = _make_webui(pending_changes=False)

    async with await _client(webui) as client:
        resp = await client.get("/api/provider-catalog")

    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["pending_changes"] is False


# ---------------------------------------------------------------------------
# POST /api/provider-catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_entry_returns_201_with_entry():
    webui = _make_webui()

    async with await _client(webui) as client:
        resp = await client.post("/api/provider-catalog", json=_ENTRY_BODY)

    assert resp.status_code == 201
    assert resp.json()["entry"]["id"] == "prov-a"


@pytest.mark.asyncio
async def test_create_entry_400_on_value_error():
    catalog = _make_catalog_mgr()
    catalog.create_entry.side_effect = ValueError("Duplicate id")
    webui = _make_webui(catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.post("/api/provider-catalog", json=_ENTRY_BODY)

    assert resp.status_code == 400
    assert "Duplicate id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_entry_422_on_missing_required_field():
    webui = _make_webui()

    async with await _client(webui) as client:
        resp = await client.post(
            "/api/provider-catalog",
            json={"id": "x"},  # missing provider_type
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/provider-catalog/{entry_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_entry_200_on_success():
    webui = _make_webui()

    async with await _client(webui) as client:
        resp = await client.put("/api/provider-catalog/prov-a", json=_ENTRY_BODY)

    assert resp.status_code == 200
    assert resp.json()["entry"]["id"] == "prov-a"


@pytest.mark.asyncio
async def test_update_entry_404_on_key_error():
    catalog = _make_catalog_mgr()
    catalog.update_entry.side_effect = KeyError("Entry 'missing' not found")
    webui = _make_webui(catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.put("/api/provider-catalog/missing", json=_ENTRY_BODY)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_entry_path_id_wins_over_body_id():
    catalog = _make_catalog_mgr()
    webui = _make_webui(catalog_mgr=catalog)

    body = {**_ENTRY_BODY, "id": "body-id"}  # body says "body-id"

    async with await _client(webui) as client:
        await client.put("/api/provider-catalog/path-id", json=body)

    # The manager should have been called with the path id, not body id
    call_args = catalog.update_entry.call_args
    assert call_args[0][0] == "path-id"  # first positional arg = entry_id
    assert call_args[0][1]["id"] == "path-id"  # body dict also has path id


# ---------------------------------------------------------------------------
# DELETE /api/provider-catalog/{entry_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_entry_200_on_success():
    webui = _make_webui()

    async with await _client(webui) as client:
        resp = await client.delete("/api/provider-catalog/prov-a")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_entry_404_on_key_error():
    catalog = _make_catalog_mgr()
    catalog.delete_entry.side_effect = KeyError("Entry 'gone' not found")
    webui = _make_webui(catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.delete("/api/provider-catalog/gone")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/provider-catalog/restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_calls_rebuild_and_clears_pending_changes():
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    proxy = _make_proxy_mgr(model_count=1, last_restart=ts)
    catalog = _make_catalog_mgr()
    webui = _make_webui(proxy_mgr=proxy, catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.post("/api/provider-catalog/restart")

    assert resp.status_code == 200
    proxy.rebuild.assert_awaited_once()
    catalog.clear_pending_changes.assert_awaited_once()

    body = resp.json()
    assert body["pending_changes"] is False
    assert body["model_count"] == 1
    assert body["last_restart"] == ts.isoformat()


@pytest.mark.asyncio
async def test_restart_500_on_rebuild_failure_does_not_clear_pending():
    proxy = _make_proxy_mgr()
    proxy.rebuild.side_effect = RuntimeError("secret not found")
    catalog = _make_catalog_mgr()
    webui = _make_webui(proxy_mgr=proxy, catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.post("/api/provider-catalog/restart")

    assert resp.status_code == 500
    catalog.clear_pending_changes.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /api/provider-catalog/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_all_six_fields():
    ts = datetime(2026, 3, 15, 10, 30, 0, tzinfo=UTC)
    proxy = _make_proxy_mgr(is_running=True, port=4000, model_count=3, last_restart=ts)
    webui = _make_webui(proxy_mgr=proxy, pending_changes=True)

    async with await _client(webui) as client:
        resp = await client.get("/api/provider-catalog/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["port"] == 4000
    assert body["pending_changes"] is True
    assert body["model_count"] == 3
    assert body["last_restart"] == ts.isoformat()
    assert body["last_error"] is None


@pytest.mark.asyncio
async def test_status_last_restart_null_when_not_set():
    proxy = _make_proxy_mgr(last_restart=None)
    webui = _make_webui(proxy_mgr=proxy)

    async with await _client(webui) as client:
        resp = await client.get("/api/provider-catalog/status")

    assert resp.status_code == 200
    assert resp.json()["last_restart"] is None


@pytest.mark.asyncio
async def test_status_last_error_populated():
    proxy = _make_proxy_mgr(last_error="ValueError: Secret 'x' not found")
    webui = _make_webui(proxy_mgr=proxy)

    async with await _client(webui) as client:
        resp = await client.get("/api/provider-catalog/status")

    assert resp.status_code == 200
    assert resp.json()["last_error"] == "ValueError: Secret 'x' not found"


# ---------------------------------------------------------------------------
# drop_params round-trip (issue #1476)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_entry_with_drop_params_true_passes_through():
    """POST with drop_params=true on a model sends it to the catalog manager."""
    catalog = _make_catalog_mgr()
    webui = _make_webui(catalog_mgr=catalog)

    body = {
        "id": "prov-b",
        "provider_type": "openai",
        "litellm_params_template": {},
        "models": [{"id": "gpt4", "litellm_model": "openai/gpt-4", "drop_params": True}],
    }

    async with await _client(webui) as client:
        resp = await client.post("/api/provider-catalog", json=body)

    assert resp.status_code == 201
    call_data = catalog.create_entry.call_args[0][0]
    assert call_data["models"][0]["drop_params"] is True


@pytest.mark.asyncio
async def test_list_entries_drop_params_returned_in_response():
    """GET returns drop_params=true when the manager includes it in an entry."""
    entry_with_drop = {
        "id": "prov-c",
        "provider_type": "openai",
        "litellm_params_template": {},
        "models": [{"id": "m1", "litellm_model": "openai/gpt-4", "drop_params": True}],
    }
    catalog = _make_catalog_mgr(entries=[entry_with_drop])
    webui = _make_webui(catalog_mgr=catalog)

    async with await _client(webui) as client:
        resp = await client.get("/api/provider-catalog")

    assert resp.status_code == 200
    model = resp.json()["entries"][0]["models"][0]
    assert model["drop_params"] is True


@pytest.mark.asyncio
async def test_create_entry_model_without_drop_params_defaults_false():
    """POST with a model missing drop_params → catalog manager receives drop_params=False."""
    catalog = _make_catalog_mgr()
    webui = _make_webui(catalog_mgr=catalog)

    body = {
        "id": "prov-d",
        "provider_type": "anthropic",
        "litellm_params_template": {},
        "models": [{"id": "m1", "litellm_model": "anthropic/claude-3"}],
    }

    async with await _client(webui) as client:
        resp = await client.post("/api/provider-catalog", json=body)

    assert resp.status_code == 201
    call_data = catalog.create_entry.call_args[0][0]
    assert call_data["models"][0]["drop_params"] is False
