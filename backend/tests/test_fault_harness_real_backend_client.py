"""Real `BackendClient`/gzip relay path variant of the fault-simulation harness
(issue #2037 Stage E, AC6 item 3).

Unlike `src/tests/test_fault_simulation.py` (which drives `FakeBackend` — an
entirely hand-rolled `get_json()` stand-in that never touches
`src/backend_client.py::BackendClient`'s gzip-decompression code at all), this
module mounts the REAL `backend.routers.poll.build_router()` on a minimal
standalone FastAPI app with the REAL `GZipMiddleware` config, and drives a
real `BackendClient` against it via `httpx.ASGITransport` (in-process, no real
sockets, but real HTTP semantics/compression) — proving gzip compress/
decompress round-trips data correctly end-to-end through the actual production
code, not just that the hand-rolled fake does.

Lives under `backend/tests/` (not `src/tests/`, despite exercising
`src/backend_client.py`) because mounting `backend.routers.poll.build_router`
requires importing `backend.*` — forbidden for anything under `src/` by
`src/tests/test_import_boundary.py`. The reverse direction (`backend/`
importing `src/`) has no such structural restriction.

Not a full re-run of the fault matrix through real HTTP — scoped to the two
behaviors a hand-rolled fake can't prove: exactly-once delivery through real
gzip, and the restart-cursor-reset signal through real HTTP/gzip.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from backend.routers.poll import build_router
from shared.event_queue import EventQueue
from src.backend_client import BackendClient
from src.tests.simulation.fault_harness import BrowserClient, FakeFrontend, wait_until


class _StandinService:
    async def get_session_exists(self, session_id: str) -> bool:
        return True


class _StandinWebUI:
    """Duck-typed stand-in for the real `BackendApp` — just enough surface
    for `backend.routers.poll.build_router()` to run against (`.ui_queue`,
    `.session_queues`, `._queue_append_hook()`, `.service.get_session_exists()`).
    `.coordinator` is deliberately omitted: `poll_session()`'s
    `mark_viewed()` call is wrapped in a broad try/except in the real router,
    so a missing attribute there is silently logged, not raised.
    """

    def __init__(self, session_id: str) -> None:
        self.ui_queue = EventQueue()
        self.session_queues: dict[str, EventQueue] = {session_id: EventQueue()}
        self.service = _StandinService()

    def _queue_append_hook(self, session_id: str):
        return lambda event: None


def _build_app(webui: _StandinWebUI) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(webui))
    # Same config as backend/web_server.py's real GZipMiddleware (issue #2029) —
    # real compression must actually trigger here, not silently no-op below a
    # mismatched threshold.
    app.add_middleware(
        GZipMiddleware,
        minimum_size=500,
        exclude_content_types=(*DEFAULT_EXCLUDED_CONTENT_TYPES, "application/octet-stream"),
    )
    return app


@pytest.fixture(autouse=True)
def _fast_poll_timing(monkeypatch):
    """Scoped-down determinism pattern, same intent as
    src/tests/test_fault_simulation.py's `_fast_poll_timing` — but unlike that
    file's `FakeBackend` (whose hand-rolled `get_json()` accepts a float
    `timeout` param), the REAL `backend.routers.poll.build_router()` endpoint
    declares `timeout: int`; a float value here gets rejected by FastAPI's
    query-param coercion with a 422 before ever reaching the relay logic. `1`
    is the smallest valid value that still exercises genuine long-poll
    waiting semantics.
    """
    monkeypatch.setattr("src.poll_relay._POLL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr("src.poll_relay._POLL_CLIENT_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.02)


@pytest.mark.asyncio
async def test_real_backend_client_response_is_actually_gzipped():
    """Sanity check that the harness's GZipMiddleware config is correctly
    wired before trusting the exactly-once test below: a large-enough batch
    of events must come back with `Content-Encoding: gzip`, not silently
    no-op below the `minimum_size=500` threshold."""
    session_id = "gzip-sanity-session"
    webui = _StandinWebUI(session_id)
    app = _build_app(webui)
    for i in range(50):
        webui.session_queues[session_id].append({"type": "test_event", "seq": i, "pad": "x" * 50})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as raw_client:
        resp = await raw_client.get(
            f"/api/poll/session/{session_id}", params={"since": 0, "timeout": 0},
            headers={"accept-encoding": "gzip"},
        )
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert len(resp.json()["events"]) == 50


@pytest.mark.asyncio
async def test_real_backend_client_delivers_exactly_once_through_real_gzip():
    """AC6: a batch of sample events, delivered through the real BackendClient
    + real poll router + real GZipMiddleware stack, must arrive exactly once —
    proving gzip compress/decompress round-trips data correctly end-to-end."""
    session_id = "real-backend-client-exactly-once-session"
    webui = _StandinWebUI(session_id)
    app = _build_app(webui)

    sample_events = [{"type": "test_event", "seq": i, "pad": "x" * 50} for i in range(50)]
    for event in sample_events:
        webui.session_queues[session_id].append(event)

    backend_client = BackendClient(
        base_url="http://test", token="test-token", transport=httpx.ASGITransport(app=app)
    )
    try:
        frontend = FakeFrontend(backend_client)
        client = BrowserClient(frontend, session_id=session_id)

        stop_event = asyncio.Event()
        poll_task = asyncio.create_task(client.run_until(stop_event, poll_timeout=0.2))
        await wait_until(lambda: len(client.received) >= len(sample_events))
        stop_event.set()
        await poll_task
        await frontend.stop()
    finally:
        await backend_client.aclose()

    assert len(client.received) == len(sample_events)
    assert len(client.received) == len(client.received_deduped())
    delivered = [event for _, event in sorted(client.received, key=lambda pair: pair[0])]
    assert delivered == sample_events


@pytest.mark.asyncio
async def test_real_backend_client_observes_restart_cursor_reset():
    """AC6: replacing the stand-in webui's session EventQueue mid-run (the
    real-router equivalent of FakeBackend.restart()) must still surface as an
    explicit reset signal to the client when going through real HTTP/gzip,
    not just FakeBackend's in-process stand-in path."""
    session_id = "real-backend-client-restart-session"
    webui = _StandinWebUI(session_id)
    app = _build_app(webui)

    pre_restart_events = [{"type": "pre", "seq": i} for i in range(10)]
    for event in pre_restart_events:
        webui.session_queues[session_id].append(event)

    backend_client = BackendClient(
        base_url="http://test", token="test-token", transport=httpx.ASGITransport(app=app)
    )
    try:
        frontend = FakeFrontend(backend_client)
        client = BrowserClient(frontend, session_id=session_id)

        stop_event = asyncio.Event()
        poll_task = asyncio.create_task(client.run_until(stop_event, poll_timeout=0.2))
        await wait_until(lambda: len(client.received) >= len(pre_restart_events))
        assert len(client.received) == len(pre_restart_events)

        # Real-router equivalent of FakeBackend.restart(): a fresh EventQueue,
        # same cursor-space discontinuity, pre-registered under the same key
        # so poll_session's "not in session_queues" branch is never hit.
        webui.session_queues[session_id] = EventQueue()
        post_restart_events = [{"type": "post", "seq": i} for i in range(5)]
        for event in post_restart_events:
            webui.session_queues[session_id].append(event)

        await wait_until(lambda: client.reset_count >= 1)
        stop_event.set()
        await poll_task
        await frontend.stop()
    finally:
        await backend_client.aclose()

    assert client.reset_count >= 1
