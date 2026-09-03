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
from unittest.mock import AsyncMock, MagicMock

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
