"""Tests for src/routers/sessions.py's GET /api/sessions/{id}/messages (epic
#1990 stage 5a, AC3) — the Frontend-issued history cursor.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.event_queue import EventQueue
from src.poll_relay import PollRelay
from src.routers.sessions import build_router


def _make_webui(get_json_side_effect):
    """`get_json_side_effect` keeps its original single-stream-shaped signature
    (`path, params, timeout`) — reused both for the `/messages` REST call
    (`src/routers/sessions.py`'s own `get_json` call, unchanged by epic #1990
    stage 5c) and, via the adapter below, for what used to be the relay's own
    single-stream poll call. The relay itself now speaks the multiplexed
    `POST /api/poll` protocol (`request_json`); the adapter translates between
    the two so every existing test's per-test side_effect body didn't need to
    change shape."""
    webui = MagicMock()
    webui.app = FastAPI()
    webui.app.include_router(build_router(webui))
    webui.backend_client.get_json = AsyncMock(side_effect=get_json_side_effect)

    async def request_json_side_effect(method, path, json=None, timeout=None):
        assert method == "POST" and path == "/api/poll"
        sessions_req = (json or {}).get("sessions") or {}
        response: dict = {"sessions": {}}
        for session_id, query in sessions_req.items():
            response["sessions"][session_id] = await get_json_side_effect(
                f"/api/poll/session/{session_id}", params={"since": query["since"]}, timeout=None,
            )
        return response

    webui.backend_client.request_json = AsyncMock(side_effect=request_json_side_effect)
    webui.backend_supervisor = None
    webui.backend_client.reachability.is_unreachable = False
    webui.poll_relay = PollRelay(webui.backend_client, EventQueue(), {})
    return webui


async def _get(webui, path: str):
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_substitutes_local_cursor_for_backend_cursor_when_already_relayed():
    """Issue #2160 AC2: Backend's own cursor numbering carries a nonzero baseline
    (100) this relay never observed directly — as if Backend already had history
    for this session before the relay's first poll of it — so the checkpoint-
    interpolated LOCAL cursor (which always starts counting from 0) can never
    coincidentally equal Backend's raw value the way the old same-baseline
    fixture did. This proves genuine substitution happened, not passthrough."""
    session_id = "sess-1"

    async def side_effect(path, params=None, timeout=None):
        if path == f"/api/sessions/{session_id}/messages":
            return {
                "messages": [{"type": "assistant"}], "total_count": 3,
                "limit": 50, "offset": 0, "has_more": False,
                "event_cursor": 101, "epoch": "e1",
            }
        # The relay's own poll — deliver 3 events once, then go quiet. next_cursor=103
        # (not 3) establishes the nonzero Backend-side baseline described above:
        # backend_cursor_before = next_cursor - len(events) = 100.
        if not getattr(side_effect, "_done", False):
            side_effect._done = True
            return {"events": [{"n": 1}, {"n": 2}, {"n": 3}], "next_cursor": 103, "epoch": "e1"}
        import asyncio
        await asyncio.sleep(100)

    webui = _make_webui(side_effect)

    resp = await _get(webui, f"/api/sessions/{session_id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    # local = local_cursor_before(0) + (backend_cursor(101) - backend_cursor_before(100)) = 1
    assert body["event_cursor"] == 1
    assert body["event_cursor"] != 101  # not Backend's raw value — genuine substitution
    assert body["messages"] == [{"type": "assistant"}]
    assert body["total_count"] == 3

    await webui.poll_relay.stop()


@pytest.mark.asyncio
async def test_brand_new_session_resolves_without_waiting_out_the_timeout():
    session_id = "sess-brandnew"

    async def side_effect(path, params=None, timeout=None):
        if path == f"/api/sessions/{session_id}/messages":
            return {
                "messages": [], "total_count": 0, "limit": 50, "offset": 0,
                "has_more": False, "event_cursor": 0, "epoch": "e1",
            }
        import asyncio
        await asyncio.sleep(100)  # relay never produces anything

    webui = _make_webui(side_effect)

    import time
    start = time.monotonic()
    resp = await _get(webui, f"/api/sessions/{session_id}/messages")
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert resp.json()["event_cursor"] == 0
    assert elapsed < 2.0  # did not wait out the 10s catch-up timeout

    await webui.poll_relay.stop()


@pytest.mark.asyncio
async def test_404_from_backend_propagates():
    session_id = "sess-missing"

    async def side_effect(path, params=None, timeout=None):
        request = httpx.Request("GET", f"http://backend{path}")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    webui = _make_webui(side_effect)

    resp = await _get(webui, f"/api/sessions/{session_id}/messages")

    assert resp.status_code == 404
    await webui.poll_relay.stop()


@pytest.mark.asyncio
async def test_limit_and_offset_are_forwarded_to_backend():
    session_id = "sess-paginated"
    seen_params = {}

    async def side_effect(path, params=None, timeout=None):
        if path == f"/api/sessions/{session_id}/messages":
            seen_params.update(params or {})
            return {
                "messages": [], "total_count": 0, "limit": 10, "offset": 5,
                "has_more": False, "event_cursor": 0, "epoch": "e1",
            }
        import asyncio
        await asyncio.sleep(100)

    webui = _make_webui(side_effect)

    resp = await _get(webui, f"/api/sessions/{session_id}/messages?limit=10&offset=5")

    assert resp.status_code == 200
    assert seen_params == {"offset": 5, "limit": 10}

    await webui.poll_relay.stop()


@pytest.mark.asyncio
async def test_stale_epoch_retries_once_then_resolves():
    """Backend's own epoch moved between the history read and wait_until_relayed
    resolving — the route must retry once with a fresh read rather than hang or
    return a wrong cursor."""
    session_id = "sess-staleretry"
    call_count = 0

    async def side_effect(path, params=None, timeout=None):
        nonlocal call_count
        if path == f"/api/sessions/{session_id}/messages":
            call_count += 1
            if call_count == 1:
                return {
                    "messages": [{"type": "assistant", "n": "stale"}], "total_count": 1,
                    "limit": 50, "offset": 0, "has_more": False,
                    "event_cursor": 50, "epoch": "e1",  # stale: relay already moved to e2
                }
            return {
                "messages": [{"type": "assistant", "n": "fresh"}], "total_count": 1,
                "limit": 50, "offset": 0, "has_more": False,
                "event_cursor": 1, "epoch": "e2",
            }
        # The relay's own poll: already on epoch e2 from the very first call.
        import asyncio
        if not getattr(side_effect, "_done", False):
            side_effect._done = True
            return {"events": [{"n": 1}], "next_cursor": 1, "epoch": "e2"}
        await asyncio.sleep(100)

    webui = _make_webui(side_effect)
    # Seed the relay's last-seen epoch as already past "e1" (simulates the
    # restart having already been observed before this request even started).
    webui.poll_relay._last_epoch["sess-staleretry"] = "e2"

    resp = await _get(webui, f"/api/sessions/{session_id}/messages")

    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == [{"type": "assistant", "n": "fresh"}]  # retried, not the stale read
    assert call_count == 2

    await webui.poll_relay.stop()
