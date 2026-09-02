"""
Tests for POST /api/sessions/{session_id}/mark-unread endpoint (issue #1597).

Covers:
  - 200 + {"success": true} for session with completion
  - 200 + {"success": false} for session without completion
  - 404 for missing session
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


def _make_webui(session_exists: bool, mark_unread_result: bool) -> MagicMock:
    webui = MagicMock()
    webui.service.get_session_exists = AsyncMock(return_value=session_exists)
    webui.coordinator.session_manager.mark_unread = AsyncMock(return_value=mark_unread_result)
    return webui


async def _client(webui) -> AsyncClient:
    from fastapi import FastAPI

    from src.routers.sessions import build_router

    app = FastAPI()
    app.include_router(build_router(webui))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_mark_unread_session_with_completion_returns_success():
    """200 + success=true when session exists and has a completion."""
    webui = _make_webui(session_exists=True, mark_unread_result=True)
    async with await _client(webui) as client:
        resp = await client.post("/api/sessions/sess-123/mark-unread")
    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    webui.coordinator.session_manager.mark_unread.assert_awaited_once_with("sess-123")


@pytest.mark.asyncio
async def test_mark_unread_session_without_completion_returns_false():
    """200 + success=false when session exists but has no completion yet."""
    webui = _make_webui(session_exists=True, mark_unread_result=False)
    async with await _client(webui) as client:
        resp = await client.post("/api/sessions/sess-no-completion/mark-unread")
    assert resp.status_code == 200
    assert resp.json() == {"success": False}


@pytest.mark.asyncio
async def test_mark_unread_missing_session_returns_404():
    """404 when session does not exist."""
    webui = _make_webui(session_exists=False, mark_unread_result=False)
    async with await _client(webui) as client:
        resp = await client.post("/api/sessions/nonexistent/mark-unread")
    assert resp.status_code == 404
    webui.coordinator.session_manager.mark_unread.assert_not_awaited()
