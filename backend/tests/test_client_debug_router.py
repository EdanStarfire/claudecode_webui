"""Tests for the client-debug router (issue #1931)."""

import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.routers.client_debug import build_router


def _make_app():
    webui = MagicMock()
    app = FastAPI()
    app.include_router(build_router(webui))
    return app


VALID_PAYLOAD = {
    "session_id": "s1",
    "browser": {"user_agent": "test-agent"},
    "submitted_at": "2026-09-15T00:00:00.000Z",
    "reason": "manual",
    "events": [
        {"ts": 1000, "source": "polling", "tag": "poll-cycle", "data": {"cursorAfter": 5}},
    ],
}


class TestSubmitClientDebugBuffer:
    @pytest.mark.asyncio
    async def test_valid_payload_returns_200_and_event_count(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/debug/client-buffer", json=VALID_PAYLOAD)
        assert r.status_code == 200
        assert r.json() == {"received": 1}

    @pytest.mark.asyncio
    async def test_valid_payload_logs_expected_content(self):
        # Attach a handler directly to the 'client_debug' logger rather than relying on
        # caplog's root-logger capture: configure_logging() (called by other tests sharing
        # this process) sets propagate=False on every registered logger, including this
        # one, so root-level capture is order-dependent. A directly-attached handler
        # always sees records regardless of that flag.
        records = []
        logger = logging.getLogger("client_debug")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        original_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            app = _make_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post("/api/debug/client-buffer", json=VALID_PAYLOAD)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(original_level)
        assert r.status_code == 200
        joined = "\n".join(record.getMessage() for record in records)
        assert "s1" in joined
        assert "manual" in joined
        assert "poll-cycle" in joined

    @pytest.mark.asyncio
    async def test_null_session_id_is_accepted(self):
        app = _make_app()
        payload = {**VALID_PAYLOAD, "session_id": None}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/debug/client-buffer", json=payload)
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_422(self):
        app = _make_app()
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "reason"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/debug/client-buffer", json=payload)
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_events_list_accepted(self):
        app = _make_app()
        payload = {**VALID_PAYLOAD, "events": []}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/debug/client-buffer", json=payload)
        assert r.status_code == 200
        assert r.json() == {"received": 0}
