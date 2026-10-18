"""Tests for POST /api/legions/{legion_id}/comms (issue #2193 AC2)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app():
    from backend.routers.legion import build_router

    webui = MagicMock()
    webui.service.get_legion = AsyncMock(return_value={"legion_id": "legion-1"})
    webui.service.get_minion_name = AsyncMock(return_value=None)
    webui.service.route_comm = AsyncMock(return_value=True)

    app = FastAPI()
    app.include_router(build_router(webui))
    return app, webui


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("comm_type", "expected_delivery"),
    [
        ("report", "when_idle"),
        ("info", "when_idle"),
        ("task", "immediate"),
        ("question", "immediate"),
    ],
)
async def test_send_comm_to_legion_delivery_defaults_by_comm_type(comm_type, expected_delivery):
    """Issue #2193 AC2: `send_comm_to_legion` has no `delivery` field to set
    explicitly — every call defers to `default_delivery_for_type()`."""
    app, webui = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/legions/legion-1/comms",
            json={"to_user": True, "content": "fyi", "comm_type": comm_type},
        )

    assert resp.status_code == 200
    sent_comm = webui.service.route_comm.call_args.args[0]
    assert sent_comm.delivery.value == expected_delivery
