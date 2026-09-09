"""Tests for src/poll_relay.py (issue #498).

Includes regression tests for two bugs found during builder-review:
1. Per-session relay tasks ran forever once started, keeping Backend's
   mark_viewed() firing on every poll indefinitely — even after every browser
   tab closed — silently defeating issue #1598's unread-detection fix. Fixed
   with an idle-timeout that stops the task when no local browser poll has
   refreshed activity recently.
2. BackendClient's HTTP timeout equaled Backend's own long-poll wait ceiling
   with zero margin, risking a client-side ReadTimeout on a normal idle poll
   response arriving right at the server's deadline. Fixed by giving the
   poll-relay's HTTP calls a timeout with margin above the server ceiling.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from shared.event_queue import EventQueue
from src.poll_relay import (
    _POLL_CLIENT_TIMEOUT_SECONDS,
    _POLL_TIMEOUT_SECONDS,
    PollRelay,
)


def _make_relay(get_json_side_effect):
    backend_client = MagicMock()
    backend_client.get_json = AsyncMock(side_effect=get_json_side_effect)
    ui_queue = EventQueue()
    session_queues: dict[str, EventQueue] = {}
    relay = PollRelay(backend_client, ui_queue, session_queues)
    return relay, backend_client, ui_queue, session_queues


def test_poll_client_timeout_has_margin_above_server_ceiling():
    """Zero margin means a response arriving right at the server's deadline races
    the client's own timeout — regression test for the exact bug, not just a
    behavioral check (see module docstring, point 2)."""
    assert _POLL_CLIENT_TIMEOUT_SECONDS > _POLL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_poll_once_passes_client_timeout_with_margin():
    relay, backend_client, _, _ = _make_relay(
        get_json_side_effect=[{"events": [], "next_cursor": 0}]
    )
    await relay._poll_once("/api/poll/ui", 0)

    backend_client.get_json.assert_awaited_once()
    _, kwargs = backend_client.get_json.call_args
    assert kwargs["timeout"] == _POLL_CLIENT_TIMEOUT_SECONDS
    assert kwargs["timeout"] > kwargs["params"]["timeout"]


@pytest.mark.asyncio
async def test_ensure_session_relay_starts_task_and_appends_events():
    events_batches = [
        {"events": [{"type": "message"}], "next_cursor": 1},
        {"events": [], "next_cursor": 1},
    ]

    async def side_effect(*args, **kwargs):
        if events_batches:
            return events_batches.pop(0)
        # Stay idle forever after the fixture is exhausted (until cancelled)
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(get_json_side_effect=side_effect)
    relay.ensure_session_relay("sess-1")

    for _ in range(100):
        if session_queues["sess-1"].events_since(0)[0]:
            break
        await asyncio.sleep(0.01)

    events, _ = session_queues["sess-1"].events_since(0)
    assert len(events) == 1
    assert events[0]["type"] == "message"

    await relay.stop()


@pytest.mark.asyncio
async def test_session_relay_stops_itself_after_idle_timeout():
    """Regression test for the mark_viewed bug: a per-session relay task must
    stop once nobody's actively polling it locally, not run forever."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return {"events": [], "next_cursor": 0}

    relay, backend_client, _, session_queues = _make_relay(get_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.ensure_session_relay("sess-idle")
        task = relay._session_tasks["sess-idle"]

        for _ in range(200):
            if task.done():
                break
            await asyncio.sleep(0.01)

        assert task.done()
        assert "sess-idle" not in relay._session_tasks
        assert "sess-idle" not in relay._session_last_activity


@pytest.mark.asyncio
async def test_ensure_session_relay_refreshes_activity_and_restarts_after_idle_stop():
    """A real browser that keeps polling must keep the relay alive; one that
    comes back after the relay idled out must get a fresh task, not a dead one."""
    async def side_effect(*args, **kwargs):
        return {"events": [], "next_cursor": 0}

    relay, backend_client, _, session_queues = _make_relay(get_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.ensure_session_relay("sess-2")
        first_task = relay._session_tasks["sess-2"]

        for _ in range(200):
            if first_task.done():
                break
            await asyncio.sleep(0.01)
        assert first_task.done()

        # Simulate a browser tab coming back after the relay idled out
        relay.ensure_session_relay("sess-2")
        second_task = relay._session_tasks["sess-2"]
        assert second_task is not first_task
        assert not second_task.done()

        await relay.stop()


@pytest.mark.asyncio
async def test_ui_relay_has_no_idle_timeout():
    """The global UI stream isn't subject to the per-session mark_viewed concern
    — it must keep running for the app's lifetime once started."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)  # avoid busy-spinning the event loop in the test
        return {"events": [], "next_cursor": 0}

    relay, backend_client, _, _ = _make_relay(get_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.start_ui_relay()
        for _ in range(50):
            if call_count > 1:
                break
            await asyncio.sleep(0.01)
        assert not relay._ui_task.done()
        assert call_count > 1  # kept polling — no idle timeout applies to the UI stream

        await relay.stop()


# --- issue #1844: RequestError vs HTTPStatusError split ---


@pytest.mark.asyncio
async def test_issue_1844_request_error_retries_silently_without_local_log(caplog):
    """A connection-level failure (Backend unreachable) must retry without a
    local log line — BackendClient.get_json() already recorded it via the
    shared reachability tracker; logging it again here on every 2s retry
    would re-flood error.log for the whole outage."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("refused")
        await asyncio.sleep(100)

    relay, backend_client, ui_queue, _ = _make_relay(get_json_side_effect=side_effect)

    with caplog.at_level(logging.WARNING, logger="src.poll_relay"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.01)
            relay.start_ui_relay()
            for _ in range(200):
                if call_count >= 2:
                    break
                await asyncio.sleep(0.01)
            await relay.stop()

    assert call_count >= 2  # retried past the failure
    assert "unreachable" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_issue_1844_http_status_error_logs_full_traceback_and_retries(caplog):
    """A genuine backend-side error response (Backend up, but its own poll
    endpoint 500ing) is not a reachability concern — keep today's
    full-traceback treatment so it stays visible as a real bug."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock(status_code=500)
            )
        await asyncio.sleep(100)

    relay, backend_client, ui_queue, _ = _make_relay(get_json_side_effect=side_effect)

    with caplog.at_level(logging.ERROR, logger="src.poll_relay"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.01)
            relay.start_ui_relay()
            for _ in range(200):
                if call_count >= 2:
                    break
                await asyncio.sleep(0.01)
            await relay.stop()

    assert call_count >= 2  # retried past the failure
    assert "Backend returned an error response" in caplog.text


# --- issue #1886: Backend-cursor passthrough (local queue adopts Backend's numbering) ---


@pytest.mark.asyncio
async def test_relay_restart_seeds_from_local_queue_cursor_not_zero():
    """A relay task restarting after an idle-stop must resume from the local
    queue's own cursor (now Backend's real numbering) instead of re-fetching
    Backend's entire backlog from since=0 and re-numbering it independently."""
    first_batch_done = asyncio.Event()

    async def first_side_effect(*args, **kwargs):
        if not first_batch_done.is_set():
            first_batch_done.set()
            return {"events": [{"n": 1}, {"n": 2}], "next_cursor": 2}
        await asyncio.sleep(0.005)  # yield control — avoid starving the event loop
        return {"events": [], "next_cursor": 2}

    relay, backend_client, _, session_queues = _make_relay(get_json_side_effect=first_side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.ensure_session_relay("sess-restart")
        task = relay._session_tasks["sess-restart"]

        for _ in range(300):
            if task.done():
                break
            await asyncio.sleep(0.01)

        queue = session_queues["sess-restart"]
        assert task.done()
        assert queue.current_cursor == 2  # matches Backend's next_cursor exactly

        seen_since_values = []
        reconnect_done = asyncio.Event()

        async def second_side_effect(*args, **kwargs):
            seen_since_values.append(kwargs["params"]["since"])
            if not reconnect_done.is_set():
                reconnect_done.set()
                return {"events": [{"n": 3}], "next_cursor": 3}
            await asyncio.sleep(0.005)  # yield control — avoid starving the event loop
            return {"events": [], "next_cursor": 3}

        backend_client.get_json.side_effect = second_side_effect

        # Simulate a browser tab reconnecting after the idle-stop.
        relay.ensure_session_relay("sess-restart")

        for _ in range(300):
            if queue.current_cursor == 3:
                break
            await asyncio.sleep(0.01)

        assert queue.current_cursor == 3
        events, next_cursor = queue.events_since(0)
        assert [e["n"] for e in events] == [1, 2, 3]  # no gap, no redelivery
        assert next_cursor == 3
        assert seen_since_values[0] == 2  # seeded from the local queue, not 0

        await relay.stop()


@pytest.mark.asyncio
async def test_end_to_end_backend_cursor_resolves_correctly_against_local_queue():
    """A Backend-space event_cursor value (as GET /api/sessions/{id}/messages
    would return) must resolve correctly against the local relay-fed queue via
    events_since() — the whole point of adopting Backend's real numbering."""
    first_batch_done = asyncio.Event()

    async def side_effect(*args, **kwargs):
        if not first_batch_done.is_set():
            first_batch_done.set()
            return {"events": [{"n": 1}, {"n": 2}, {"n": 3}], "next_cursor": 3}
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(get_json_side_effect=side_effect)
    relay.ensure_session_relay("sess-e2e")

    for _ in range(300):
        if session_queues["sess-e2e"].current_cursor == 3:
            break
        await asyncio.sleep(0.01)

    queue = session_queues["sess-e2e"]
    assert queue.current_cursor == 3  # what GET /messages' event_cursor would report

    # A browser that just bootstrapped via GET /messages resumes from that
    # exact Backend-space cursor — no gap, no redelivery.
    events, next_cursor = queue.events_since(3)
    assert events == []
    assert next_cursor == 3

    events, next_cursor = queue.events_since(1)
    assert [e["n"] for e in events] == [2, 3]
    assert next_cursor == 3

    await relay.stop()


@pytest.mark.asyncio
async def test_backend_restart_regression_delivers_next_event_exactly_once():
    """Simulates a Backend process restart mid-poll: backend/web_server.py
    recreates a fresh EventQueue() (cursor 0) for the session, so the relay
    starts seeing low Backend cursor numbers again while the local queue still
    holds the old, higher range. Must not raise, and the first genuinely new
    event past the restart must be delivered exactly once — not dropped as a
    false-duplicate, not delivered twice."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"events": [{"n": "pre-1"}, {"n": "pre-2"}], "next_cursor": 100}
        if call_count == 2:
            # Backend restarted: fresh queue, no events yet, low current cursor.
            return {"events": [], "next_cursor": 0}
        if call_count == 3:
            # First genuinely new event generated after the restart.
            return {"events": [{"n": "post-1"}], "next_cursor": 1}
        await asyncio.sleep(0.005)  # yield control — avoid starving the event loop
        return {"events": [], "next_cursor": 1}

    relay, backend_client, _, session_queues = _make_relay(get_json_side_effect=side_effect)
    relay.ensure_session_relay("sess-regress")

    for _ in range(300):
        if session_queues["sess-regress"].current_cursor == 1:
            break
        await asyncio.sleep(0.01)

    queue = session_queues["sess-regress"]
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == ["post-1"]  # stale pre-restart history dropped
    assert next_cursor == 1

    await relay.stop()
