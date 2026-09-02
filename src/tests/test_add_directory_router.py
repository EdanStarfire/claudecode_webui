"""Tests for POST /api/sessions/{session_id}/add-directory (issue #1675)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app():
    from src.routers.session_runtime import build_router

    webui = MagicMock()
    webui.service.get_session_exists = AsyncMock(return_value=True)
    webui.coordinator.add_directory = AsyncMock()

    app = FastAPI()
    app.include_router(build_router(webui))
    return app, webui


@pytest.mark.asyncio
async def test_add_directory_success():
    app, webui = _make_app()
    webui.coordinator.add_directory.return_value = {"directory": "/test/project/subdir"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/sessions/sess-1/add-directory", json={"directory": "/test/project/subdir"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["directory"] == "/test/project/subdir"
    webui.coordinator.add_directory.assert_called_once_with("sess-1", "/test/project/subdir")


@pytest.mark.asyncio
async def test_add_directory_docker_session_returns_400():
    app, webui = _make_app()
    webui.coordinator.add_directory.side_effect = ValueError(
        "Adding directories to a running Docker session requires a restart"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/sessions/sess-1/add-directory", json={"directory": "/test/project/subdir"}
        )

    assert resp.status_code == 400
    assert "restart" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_directory_invalid_path_returns_400():
    app, webui = _make_app()
    webui.coordinator.add_directory.side_effect = ValueError(
        "Directory must be a subdirectory of the session's working directory or an already-registered directory: /elsewhere"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/sessions/sess-1/add-directory", json={"directory": "/elsewhere"}
        )

    assert resp.status_code == 400
    assert "subdirectory" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_directory_session_not_found_returns_404():
    app, webui = _make_app()
    webui.service.get_session_exists = AsyncMock(return_value=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/sessions/missing/add-directory", json={"directory": "/test/project/subdir"}
        )

    assert resp.status_code == 404
    webui.coordinator.add_directory.assert_not_called()
