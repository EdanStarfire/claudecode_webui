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

Epic #1990 stage 5c: `PollRelay` was restructured from N per-stream tasks (one
per session, one for UI, each self-stopping when idle) into ONE consolidated
task carrying a dynamic subscription set recomputed every cycle. The "idle
timeout" regression test from point 1 above is preserved, but its mechanism
changed: a session aging out no longer stops a task (there's only one task,
and it never stops) — it drops out of the next request body AND the relay's
own per-stream bookkeeping dicts are explicitly pruned (US3/T3, epic #1990
stage 5c). `ensure_session_relay()` is renamed `touch_session()` — not kept as
a compat alias.
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from shared.event_queue import EventQueue
from src.poll_relay import (
    _POLL_CLIENT_TIMEOUT_SECONDS,
    _POLL_TIMEOUT_SECONDS,
    FRONTEND_PROCESS_EPOCH,
    PollRelay,
    _Checkpoint,
)


def _make_relay(request_json_side_effect):
    backend_client = MagicMock()
    backend_client.request_json = AsyncMock(side_effect=request_json_side_effect)
    ui_queue = EventQueue()
    session_queues: dict[str, EventQueue] = {}
    relay = PollRelay(backend_client, ui_queue, session_queues)
    return relay, backend_client, ui_queue, session_queues


def _batch(*, ui=None, sessions=None) -> dict:
    """Build a multiplexed /api/poll-shaped response body."""
    body: dict = {}
    if ui is not None:
        body["ui"] = ui
    body["sessions"] = sessions or {}
    return body


def _stream(events=None, next_cursor=0, epoch="e1", evicted=False) -> dict:
    return {"events": events or [], "next_cursor": next_cursor, "epoch": epoch, "evicted": evicted}


async def _wait_until(predicate, attempts=300, interval=0.01) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


def test_poll_client_timeout_has_margin_above_server_ceiling():
    """Zero margin means a response arriving right at the server's deadline races
    the client's own timeout — regression test for the exact bug, not just a
    behavioral check (see module docstring, point 2)."""
    assert _POLL_CLIENT_TIMEOUT_SECONDS > _POLL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_poll_once_posts_multiplex_body_with_client_timeout_margin():
    relay, backend_client, _, _ = _make_relay(
        request_json_side_effect=[_batch(ui=_stream()), _batch(ui=_stream())]
    )
    relay.start_ui_relay()
    await _wait_until(lambda: backend_client.request_json.await_count >= 2)

    calls = backend_client.request_json.call_args_list
    args, kwargs = calls[0]
    assert args[0] == "POST"
    assert args[1] == "/api/poll"
    assert kwargs["timeout"] == _POLL_CLIENT_TIMEOUT_SECONDS
    assert "ui" in kwargs["json"]
    # Issue #2161: the very first-ever request is one-shot fast-probed (json timeout:0),
    # so the margin check below uses the SECOND (steady-state) request instead, where
    # json["timeout"] has reverted to _POLL_TIMEOUT_SECONDS (30) — the actual server
    # long-poll ceiling this margin exists to protect against racing with zero slack.
    _, second_kwargs = calls[1]
    assert second_kwargs["json"]["timeout"] == _POLL_TIMEOUT_SECONDS
    assert second_kwargs["timeout"] > second_kwargs["json"]["timeout"]

    await relay.stop()


@pytest.mark.asyncio
async def test_touch_session_one_shot_fast_probe_timeout():
    """Issue #2161: mirrors the identical one-shot mechanism on the browser tier
    (frontend/src/stores/polling.js). touch_session() on a brand-new session must
    produce a request whose body has timeout:0 — the fast probe — and the NEXT
    cycle's request (nothing new touched) must revert to _POLL_TIMEOUT_SECONDS.
    Checked through a THIRD cycle too (not just first vs. second), since the
    one-shot discipline is the single point of failure for this whole mechanism:
    if the flag isn't reliably cleared, every subsequent cycle degrades into a
    0-timeout busy-poll against Backend — worse than the bug being fixed."""
    request_bodies = []

    async def side_effect(*args, **kwargs):
        # A real network round trip (matching
        # test_rapid_touch_of_many_new_sessions_does_not_starve_the_loop's template),
        # not an instant-return mock, so the loop actually completes multiple
        # distinct cycles rather than this coroutine racing _wait_until.
        await asyncio.sleep(0.01)
        request_bodies.append(kwargs["json"])
        return _batch(ui=_stream(), sessions={"sess-fp": _stream()})

    relay, backend_client, _, _ = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-fp")

    await _wait_until(lambda: len(request_bodies) >= 3)

    assert request_bodies[0]["timeout"] == 0
    assert request_bodies[1]["timeout"] == _POLL_TIMEOUT_SECONDS
    assert request_bodies[2]["timeout"] == _POLL_TIMEOUT_SECONDS

    await relay.stop()


@pytest.mark.asyncio
async def test_touch_session_appends_events_from_subscription_response():
    events_batches = [
        _batch(ui=_stream(), sessions={"sess-1": _stream(events=[{"type": "message"}], next_cursor=1)}),
    ]

    async def side_effect(*args, **kwargs):
        if events_batches:
            return events_batches.pop(0)
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-1")

    await _wait_until(lambda: session_queues["sess-1"].events_since(0)[0])

    events, _, _ = session_queues["sess-1"].events_since(0)
    assert len(events) == 1
    assert events[0]["type"] == "message"

    await relay.stop()


@pytest.mark.asyncio
async def test_single_task_serves_both_ui_and_session_subscriptions():
    """The consolidated task must request both streams at once — exactly one
    HTTP call per cycle regardless of how many streams are subscribed."""
    calls = []

    async def side_effect(*args, **kwargs):
        calls.append(kwargs["json"])
        if len(calls) == 1:
            return _batch(ui=_stream(), sessions={"sess-a": _stream(), "sess-b": _stream()})
        await asyncio.sleep(100)

    relay, backend_client, _, _ = _make_relay(request_json_side_effect=side_effect)
    relay.start_ui_relay()
    relay.touch_session("sess-a")
    relay.touch_session("sess-b")

    await _wait_until(lambda: len(calls) >= 1)
    await asyncio.sleep(0.02)  # let a stray second cycle happen if the loop is misbehaving

    # Only one underlying task exists: calls may happen more than once as the
    # loop cycles, but the first body must carry every subscribed stream at once.
    first_body = calls[0]
    assert set(first_body["sessions"].keys()) == {"sess-a", "sess-b"}
    assert "ui" in first_body

    await relay.stop()


@pytest.mark.asyncio
async def test_session_subscription_ages_out_without_stopping_the_task():
    """Epic #1990 stage 5c: a session dropping out of the subscription set no
    longer stops any task (there is only one, consolidated task, and it never
    stops) — it's simply omitted from the next request body."""
    seen_session_keys = []

    async def side_effect(*args, **kwargs):
        seen_session_keys.append(set(kwargs["json"]["sessions"].keys()))
        await asyncio.sleep(0.01)
        return _batch(ui=_stream(), sessions={sid: _stream() for sid in kwargs["json"]["sessions"]})

    relay, backend_client, _, _ = _make_relay(request_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        # ui stays subscribed forever (no idle timeout applies to it), so the
        # relay keeps polling — and thus keeps calling side_effect — after the
        # session ages out, giving this test an observable later call to assert
        # against. Without this, subscription would go fully empty on age-out
        # and the loop would stop calling the backend at all (nothing left to
        # poll for), freezing seen_session_keys at its last pre-age-out value.
        relay.start_ui_relay()
        relay.touch_session("sess-idle")
        await _wait_until(lambda: len(seen_session_keys) >= 1)
        assert "sess-idle" in seen_session_keys[-1]

        # Let the session age out with no further touch_session() calls.
        await _wait_until(
            lambda: len(seen_session_keys) >= 1 and "sess-idle" not in seen_session_keys[-1],
            attempts=500,
        )
        assert "sess-idle" not in seen_session_keys[-1]

        # The relay task itself is still alive and serving the UI stream.
        assert relay._task is not None and not relay._task.done()

        await relay.stop()


@pytest.mark.asyncio
async def test_rapid_touch_of_many_new_sessions_does_not_starve_the_loop():
    """WebUI-Agent review (stage 5c, Phase 2 checkpoint): the cancel-and-restart
    interruption mechanism (touch_session()/start_ui_relay() cancelling the
    in-flight _poll_once() task) raises a thrashing question under rapid
    subscription churn — if session A is touched (cancel+restart), then B is
    touched 10ms later before that restart produced anything, does it cancel
    again, and can enough rapid touches in a row prevent the task from EVER
    completing a useful poll cycle? This asserts it can't: touching 5 brand-new
    sessions back-to-back with no yield in between (each touch is synchronous —
    nothing here awaits between them) must still converge to one completed
    cycle, carrying every touched session, within a small bounded time — not
    hang indefinitely re-cancelling forever."""
    completed_cycle_session_keys = []

    async def side_effect(*args, **kwargs):
        # A real network round trip, unlike the instant-return mocks elsewhere in
        # this file — needed so an in-flight call actually HAS a window in which
        # a rapid-fire touch can interrupt it (an instantly-resolving mock would
        # never be interruptible, defeating the point of this test).
        await asyncio.sleep(0.02)
        sessions_req = kwargs["json"]["sessions"]
        completed_cycle_session_keys.append(set(sessions_req.keys()))
        return _batch(ui=_stream(), sessions={sid: _stream() for sid in sessions_req})

    relay, backend_client, _, _ = _make_relay(request_json_side_effect=side_effect)
    relay.start_ui_relay()

    # Rapid-fire touch of 5 distinct brand-new sessions, no await in between —
    # each one is "new" (not yet in _active_subscription), so each triggers
    # _interrupt_if_in_flight() on whatever the loop is currently doing.
    new_session_ids = [f"sess-churn-{i}" for i in range(5)]
    for sid in new_session_ids:
        relay.touch_session(sid)

    converged = await _wait_until(
        lambda: completed_cycle_session_keys
        and set(new_session_ids) <= completed_cycle_session_keys[-1],
        attempts=200,  # 200 * 0.01s = 2s ceiling — generous vs. the mock's 0.02s RTT
    )
    assert converged, (
        f"loop never completed a cycle covering every touched session; "
        f"completed cycles so far: {completed_cycle_session_keys}"
    )
    # Bounded, not unbounded: converging shouldn't have required anywhere near
    # the attempts ceiling given the mock's RTT is 1/10th of the poll interval.
    assert len(completed_cycle_session_keys) < 50

    await relay.stop()


@pytest.mark.asyncio
async def test_us3_aged_out_session_bookkeeping_is_pruned_directly():
    """US3/T3: after a session ages out, every one of the relay's own per-stream
    bookkeeping dicts must no longer contain that session_id — asserted
    directly, not inferred from event-correctness alone. (Issue #2305: `_backend_cursor`
    and `_last_epoch` are the deliberate exceptions — kept for resume.)"""
    delivered = False

    async def side_effect(*args, **kwargs):
        nonlocal delivered
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)  # always yield — a real Backend call always awaits I/O
        if "sess-prune" in sessions_req and not delivered:
            delivered = True
            return _batch(
                ui=_stream(),
                sessions={"sess-prune": _stream(events=[{"type": "message"}], next_cursor=1, epoch="e1")},
            )
        # Idle Backend: echo back whatever cursor was requested, no progress.
        return _batch(ui=_stream(), sessions={
            sid: _stream(next_cursor=query["since"], epoch="e1") for sid, query in sessions_req.items()
        })

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.touch_session("sess-prune")
        await _wait_until(lambda: session_queues["sess-prune"].current_cursor >= 1)

        assert "sess-prune" in relay._session_last_activity
        assert "sess-prune" in relay._last_epoch
        assert "sess-prune" in relay._backend_cursor
        assert "sess-prune" in relay._checkpoints
        assert "sess-prune" in relay._frontier

        await _wait_until(lambda: "sess-prune" not in relay._session_last_activity, attempts=500)

        assert "sess-prune" not in relay._session_last_activity
        # Issue #2305: cursor + epoch are retained across age-out so re-entry resumes.
        assert relay._last_epoch["sess-prune"] == "e1"
        assert relay._backend_cursor["sess-prune"] == 1
        assert relay._aged_out["sess-prune"] == 1
        assert "sess-prune" not in relay._checkpoints
        assert "sess-prune" not in relay._pending_evicted
        assert "sess-prune" not in relay._frontier

        await relay.stop()


@pytest.mark.asyncio
async def test_touch_session_after_aging_out_resumes_from_persisted_backend_cursor():
    """A session that ages out and is touched again later must resume Backend
    polling from exactly where this relay last left off — not from 0."""
    resume_since_value = []
    delivered_batches = {"first": False, "second": False}

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)  # always yield — a real Backend call always awaits I/O
        if "sess-resume" not in sessions_req:
            return _batch(ui=_stream(), sessions={})
        since = sessions_req["sess-resume"]["since"]
        if not delivered_batches["first"]:
            delivered_batches["first"] = True
            return _batch(ui=_stream(), sessions={"sess-resume": _stream(events=[{"n": 1}, {"n": 2}], next_cursor=2)})
        if not delivered_batches["second"] and since == 2:
            delivered_batches["second"] = True
            resume_since_value.append(since)  # the cursor the RESUMED request was seeded with
            return _batch(ui=_stream(), sessions={"sess-resume": _stream(events=[{"n": 3}], next_cursor=3)})
        # Idle Backend thereafter: echo back whatever cursor was requested.
        return _batch(ui=_stream(), sessions={"sess-resume": _stream(next_cursor=since)})

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.touch_session("sess-resume")
        await _wait_until(lambda: session_queues["sess-resume"].current_cursor == 2)

        # Let it age out of the subscription set.
        await _wait_until(lambda: "sess-resume" not in relay._session_last_activity, attempts=500)

        # Simulate a browser tab reconnecting after the age-out.
        relay.touch_session("sess-resume")
        await _wait_until(lambda: session_queues["sess-resume"].current_cursor == 3)

        events, next_cursor, _ = session_queues["sess-resume"].events_since(0)
        assert [e["n"] for e in events] == [1, 2, 3]  # no gap, no redelivery
        assert next_cursor == 3
        # The resumed request was seeded from the persisted Backend-cursor (2),
        # not from 0.
        assert resume_since_value == [2]

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
        return _batch(ui=_stream())

    relay, backend_client, _, _ = _make_relay(request_json_side_effect=side_effect)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.start_ui_relay()
        await _wait_until(lambda: call_count > 1, attempts=50)
        assert not relay._task.done()
        assert call_count > 1  # kept polling — no idle timeout applies to the UI stream

        await relay.stop()


# --- issue #1844: RequestError vs HTTPStatusError split ---


@pytest.mark.asyncio
async def test_issue_1844_request_error_retries_silently_without_local_log(caplog):
    """A connection-level failure (Backend unreachable) must retry without a
    local log line — BackendClient.request_json() already recorded it via the
    shared reachability tracker; logging it again here on every 2s retry
    would re-flood error.log for the whole outage."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("refused")
        await asyncio.sleep(100)

    relay, backend_client, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)

    with caplog.at_level(logging.WARNING, logger="src.poll_relay"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.01)
            relay.start_ui_relay()
            await _wait_until(lambda: call_count >= 2)
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

    relay, backend_client, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)

    with caplog.at_level(logging.ERROR, logger="src.poll_relay"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.01)
            relay.start_ui_relay()
            await _wait_until(lambda: call_count >= 2)
            await relay.stop()

    assert call_count >= 2  # retried past the failure
    assert "Backend returned an error response" in caplog.text


# --- epic #1990 stage 5a: relay-owned Backend-cursor tracking (replaces #1886's adoption) ---


@pytest.mark.asyncio
async def test_backend_restart_emits_stream_reset_and_preserves_pre_restart_history():
    """Epic #1990 stage 5a: a Backend restart is now surfaced as an explicit
    `stream_reset` event appended to the (otherwise untouched) local queue — pre-
    restart history is no longer dropped (that was the old cursor-adoption
    behavior this stage removes), and the first genuinely new post-restart event
    is still delivered exactly once, right after the marker."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _batch(ui=_stream(), sessions={
                "sess-regress": _stream(events=[{"n": "pre-1"}, {"n": "pre-2"}], next_cursor=100, epoch="e1")
            })
        if call_count == 2:
            # Backend restarted: fresh queue, no events yet, low current cursor,
            # but a DIFFERENT epoch — the signal the relay now actually watches.
            return _batch(ui=_stream(), sessions={"sess-regress": _stream(events=[], next_cursor=0, epoch="e2")})
        if call_count == 3:
            # First genuinely new event generated after the restart.
            return _batch(ui=_stream(), sessions={
                "sess-regress": _stream(events=[{"n": "post-1"}], next_cursor=1, epoch="e2")
            })
        await asyncio.sleep(0.005)  # yield control — avoid starving the event loop
        return _batch(ui=_stream(), sessions={"sess-regress": _stream(events=[], next_cursor=1, epoch="e2")})

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-regress")

    await _wait_until(lambda: session_queues["sess-regress"].current_cursor >= 4)

    queue = session_queues["sess-regress"]
    events, next_cursor, _ = queue.events_since(0)
    assert len(events) == 4
    assert [events[0]["n"], events[1]["n"]] == ["pre-1", "pre-2"]  # preserved, not dropped
    assert events[2]["type"] == "stream_reset"
    assert events[2]["data"] == {"old_epoch": "e1", "new_epoch": "e2"}
    assert events[3]["n"] == "post-1"
    assert next_cursor == 4  # local auto-increment cursor: 2 pre + 1 marker + 1 post

    await relay.stop()


# --- issue #2160 AC1: Frontend-tier restart seeding ---


@pytest.mark.asyncio
async def test_touch_session_seeds_stream_reset_when_resuming_a_stale_cursor():
    """Issue #2160 AC1: touch_session()'s first-ever call for a never-before-seen
    session_id with since > 0 is proof the caller already holds a cursor from
    before this Frontend process existed — seed the fresh queue's very first
    entry with an explicit stream_reset marker before anything else is appended."""
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)

    relay, _, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("s", since=50)

    queue = session_queues["s"]
    events, next_cursor, _ = queue.events_since(0)
    assert len(events) == 1
    assert events[0]["type"] == "stream_reset"
    assert events[0]["data"] == {"old_epoch": "", "new_epoch": FRONTEND_PROCESS_EPOCH}
    assert next_cursor == 1

    await relay.stop()


@pytest.mark.asyncio
async def test_touch_session_does_not_seed_stream_reset_for_a_brand_new_session():
    """A genuinely new session's first-ever poll always arrives with since=0 — no
    prior cursor to resync from, so no marker is seeded."""
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)

    relay, _, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("s2", since=0)

    queue = session_queues["s2"]
    events, _, _ = queue.events_since(0)
    assert events == []

    await relay.stop()


@pytest.mark.asyncio
async def test_start_ui_relay_seeds_stream_reset_when_resuming_a_stale_cursor():
    """Mirrors the touch_session() seeding test above for the UI stream's own
    first-ever-call path."""
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)

    relay, _, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)
    relay.start_ui_relay(since=50)

    events, next_cursor, _ = ui_queue.events_since(0)
    assert len(events) == 1
    assert events[0]["type"] == "stream_reset"
    assert events[0]["data"] == {"old_epoch": "", "new_epoch": FRONTEND_PROCESS_EPOCH}
    assert next_cursor == 1

    await relay.stop()


@pytest.mark.asyncio
async def test_boot_time_warm_ui_relay_does_not_consume_the_real_seed_window():
    """Regression test (issue #2160 AC1, builder-review finding): `warm_ui_relay()` is
    the boot-time-only entry point `src/web_server.py` calls unconditionally before any
    real browser ever connects. Before this fix, `start_ui_relay()`'s seed check was
    gated on the SAME `_ui_started` flag that boot's own call set `True` — so that
    since=0 boot call permanently consumed the one-shot seed-eligibility window, and a
    real resuming browser's later `start_ui_relay(since=...)` call could never seed a
    `stream_reset`, no matter how stale its `since`. Reproduces the exact real call
    order (`warm_ui_relay()` first, mirroring Frontend startup, THEN the real poll
    router's `start_ui_relay(since=...)`) and asserts the marker IS seeded."""
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)

    relay, _, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)

    relay.warm_ui_relay()  # simulates src/web_server.py's unconditional boot call
    assert ui_queue.current_cursor == 0  # boot warm-up alone must never seed anything

    relay.start_ui_relay(since=137)  # simulates the real browser's first post-restart poll

    events, next_cursor, _ = ui_queue.events_since(0)
    assert len(events) == 1
    assert events[0]["type"] == "stream_reset"
    assert events[0]["data"] == {"old_epoch": "", "new_epoch": FRONTEND_PROCESS_EPOCH}
    assert next_cursor == 1

    await relay.stop()


@pytest.mark.asyncio
async def test_start_ui_relay_seed_window_is_one_shot_even_across_repeated_calls():
    """A second start_ui_relay() call (regardless of its own since value) must not
    seed again — the seed-eligibility window (`_ui_seed_checked`) is consumed by the
    first start_ui_relay() call specifically, independent of `_ui_started`."""
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)

    relay, _, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)

    relay.start_ui_relay(since=50)
    assert ui_queue.current_cursor == 1  # first call seeds exactly once

    relay.start_ui_relay(since=999)
    assert ui_queue.current_cursor == 1  # second call, even with a nonzero since, no-ops

    await relay.stop()


# --- issue #1890: ui_queue local-write / relay-cursor collision ---


@pytest.mark.asyncio
async def test_ui_relay_uses_auto_increment_not_backend_cursor():
    """The UI queue has no Backend-derived cursor contract — no REST endpoint
    ever hands the browser a "global event cursor" to resolve against
    (unlike sessions' event_cursor). The relay must number ui_queue events
    with its own auto-increment, not adopt Backend's next_cursor verbatim."""
    first_batch_done = asyncio.Event()

    async def side_effect(*args, **kwargs):
        if not first_batch_done.is_set():
            first_batch_done.set()
            return _batch(ui=_stream(events=[{"type": "message", "n": 1}], next_cursor=500))
        await asyncio.sleep(100)

    relay, backend_client, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)
    relay.start_ui_relay()

    await _wait_until(lambda: ui_queue.current_cursor)

    assert ui_queue.current_cursor == 1  # local auto-increment, not Backend's next_cursor=500
    events, next_cursor, _ = ui_queue.events_since(0)
    assert [e["n"] for e in events] == [1]
    assert next_cursor == 1

    await relay.stop()


@pytest.mark.asyncio
async def test_ui_queue_local_write_and_relay_write_do_not_collide():
    """Core regression test for #1890: a direct local write (simulating
    /api/system/restart's ui_queue.append) and a relay-delivered event on the
    SAME EventQueue instance must not collide. Under the pre-fix
    cursor-adoption behavior, a relay event whose computed Backend-derived
    cursor happened to equal the queue's already-bumped local cursor would be
    silently dropped as a duplicate (shared/event_queue.py's dedup branch)."""
    first_batch_done = asyncio.Event()

    async def side_effect(*args, **kwargs):
        if not first_batch_done.is_set():
            first_batch_done.set()
            # Chosen so pre-fix cursor-adoption arithmetic (next_cursor - len + 1)
            # would land exactly on 1 — the cursor the direct write below already
            # bumped to, triggering the dedup-skip branch and dropping this event.
            return _batch(ui=_stream(events=[{"type": "message", "n": "relay-1"}], next_cursor=1))
        await asyncio.sleep(100)

    relay, backend_client, ui_queue, _ = _make_relay(request_json_side_effect=side_effect)

    # Simulate /api/system/restart's direct local write happening first.
    ui_queue.append({"type": "server_restarting", "n": "restart-notice"})
    assert ui_queue.current_cursor == 1

    relay.start_ui_relay()

    await _wait_until(lambda: len(ui_queue.events_since(0)[0]) >= 2)

    events, next_cursor, _ = ui_queue.events_since(0)
    assert [e["n"] for e in events] == ["restart-notice", "relay-1"]  # no drop, no reset
    assert next_cursor == 2

    await relay.stop()


# --- epic #1990 stage 5a, phase 5a-4: checkpoint interpolation / wait_until_relayed ---


@pytest.mark.asyncio
async def test_wait_until_relayed_resolves_immediately_when_already_caught_up():
    first_batch_done = asyncio.Event()

    async def side_effect(*args, **kwargs):
        if not first_batch_done.is_set():
            first_batch_done.set()
            return _batch(ui=_stream(), sessions={
                "sess-wait": _stream(events=[{"n": 1}, {"n": 2}, {"n": 3}], next_cursor=3, epoch="e1")
            })
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-wait")

    await _wait_until(lambda: session_queues["sess-wait"].current_cursor == 3)

    # K falls strictly inside the batch, not just on a boundary.
    result = await relay.wait_until_relayed("sess-wait", backend_cursor=2, epoch="e1", timeout=1.0)
    assert result.outcome == "resolved"
    assert result.local_cursor == 2  # 2nd of 3 events, 1:1 correspondence within one epoch

    result_start = await relay.wait_until_relayed("sess-wait", backend_cursor=0, epoch="e1", timeout=1.0)
    assert result_start.outcome == "resolved"
    assert result_start.local_cursor == 0

    result_end = await relay.wait_until_relayed("sess-wait", backend_cursor=3, epoch="e1", timeout=1.0)
    assert result_end.outcome == "resolved"
    assert result_end.local_cursor == 3

    await relay.stop()


@pytest.mark.asyncio
async def test_wait_until_relayed_bootstrap_brand_new_session_resolves_to_zero():
    """K=0 (a session whose history was just read for the very first time, nothing
    has happened yet) must resolve immediately — must not wait out the full
    catch-up timeout just because no checkpoint has ever been recorded."""

    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-brandnew")

    start = time.monotonic()
    result = await relay.wait_until_relayed("sess-brandnew", backend_cursor=0, epoch="e1", timeout=5.0)
    elapsed = time.monotonic() - start

    assert result.outcome == "resolved"
    assert result.local_cursor == 0
    assert elapsed < 1.0  # did not wait out the timeout

    await relay.stop()


@pytest.mark.asyncio
async def test_wait_until_relayed_quiet_gap_resolves_via_frontier():
    """A requested backend_cursor the relay has already reached/passed via empty
    polls (nothing happened in between, so no checkpoint was recorded for that
    span) must still resolve — via the relay's current frontier, not a
    checkpoint interpolation."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _batch(ui=_stream(), sessions={"sess-quiet": _stream(events=[{"n": 1}], next_cursor=1, epoch="e1")})
        if call_count == 2:
            # Backend advanced, nothing new.
            return _batch(ui=_stream(), sessions={"sess-quiet": _stream(events=[], next_cursor=5, epoch="e1")})
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-quiet")

    await _wait_until(lambda: call_count >= 2)
    await asyncio.sleep(0.02)  # let the 2nd iteration's frontier update land

    result = await relay.wait_until_relayed("sess-quiet", backend_cursor=5, epoch="e1", timeout=1.0)
    assert result.outcome == "resolved"
    assert result.local_cursor == 1  # unchanged since the one real event was relayed

    await relay.stop()


@pytest.mark.asyncio
async def test_wait_until_relayed_waits_for_relay_to_catch_up():
    """K requested ahead of what the relay has seen so far must actually wait,
    then resolve once the relay catches up — not resolve prematurely."""
    release = asyncio.Event()
    first_batch_done = asyncio.Event()

    async def side_effect(*args, **kwargs):
        if not release.is_set():
            await release.wait()
        if not first_batch_done.is_set():
            first_batch_done.set()
            return _batch(ui=_stream(), sessions={
                "sess-catchup": _stream(events=[{"n": 1}, {"n": 2}], next_cursor=2, epoch="e1")
            })
        await asyncio.sleep(100)  # yield control — avoid starving the event loop

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-catchup")

    wait_task = asyncio.create_task(
        relay.wait_until_relayed("sess-catchup", backend_cursor=2, epoch="e1", timeout=2.0)
    )
    await asyncio.sleep(0.05)
    assert not wait_task.done()  # relay hasn't produced anything yet

    release.set()
    result = await wait_task
    assert result.outcome == "resolved"
    assert result.local_cursor == 2

    await relay.stop()


@pytest.mark.asyncio
async def test_wait_until_relayed_aborts_on_confirmed_stale_epoch():
    """If the stream's epoch moved past the requested one BEFORE the wait even
    started, confirm that after the short grace window (not the full timeout) —
    that generation will never be seen again. The grace window itself exists for
    the OTHER direction (relay merely hasn't observed the requested epoch YET);
    see test_wait_until_relayed_grace_window_lets_relay_catch_up_without_falsely_
    aborting below."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _batch(ui=_stream(), sessions={"sess-staleepoch": _stream(events=[{"n": 1}], next_cursor=1, epoch="e1")})
        if call_count == 2:
            # Backend restarted before the relay ever reached backend_cursor=50.
            return _batch(ui=_stream(), sessions={"sess-staleepoch": _stream(events=[], next_cursor=0, epoch="e2")})
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-staleepoch")

    await _wait_until(lambda: call_count >= 2)
    await asyncio.sleep(0.02)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._STALE_EPOCH_GRACE_SECONDS", 0.05)
        start = time.monotonic()
        result = await relay.wait_until_relayed("sess-staleepoch", backend_cursor=50, epoch="e1", timeout=5.0)
        elapsed = time.monotonic() - start

    assert result.outcome == "stale_epoch"
    assert result.local_cursor is None
    assert elapsed < 1.0  # confirmed quickly via the (shrunk) grace window, not the full 5.0s timeout

    await relay.stop()


@pytest.mark.asyncio
async def test_wait_until_relayed_grace_window_lets_relay_catch_up_without_falsely_aborting():
    """The OTHER direction finding 2 (epic #1990 stage 5a review) fixes: a fresh
    direct-to-Backend read can beat this relay's own next poll cycle, so a
    mismatch against last-seen epoch at the very START of the wait must not
    immediately abort — give the relay a short, bounded chance to observe the
    requested epoch itself before concluding genuine staleness."""
    call_count = 0
    release_second_poll = asyncio.Event()

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _batch(ui=_stream(), sessions={"sess-gracecatchup": _stream(events=[{"n": 1}], next_cursor=1, epoch="e1")})
        if call_count == 2:
            # The relay's own poll observing the SAME epoch the caller already
            # knows about — simulates it catching up shortly after a fresh
            # Backend read already landed on e2.
            await release_second_poll.wait()
            return _batch(ui=_stream(), sessions={"sess-gracecatchup": _stream(events=[{"n": 2}], next_cursor=2, epoch="e2")})
        await asyncio.sleep(100)

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-gracecatchup")

    await _wait_until(lambda: call_count >= 2)
    # At this point last_epoch is still "e1" (the relay is parked awaiting
    # release_second_poll) while the caller already knows Backend is on "e2".

    wait_task = asyncio.create_task(
        relay.wait_until_relayed("sess-gracecatchup", backend_cursor=1, epoch="e2", timeout=5.0)
    )
    await asyncio.sleep(0.05)
    assert not wait_task.done()  # must not have falsely aborted as stale yet

    release_second_poll.set()
    result = await wait_task

    assert result.outcome == "resolved"
    assert result.local_cursor == 2

    await relay.stop()


@pytest.mark.asyncio
async def test_wait_until_relayed_falls_back_to_current_cursor_on_timeout():
    async def side_effect(*args, **kwargs):
        await asyncio.sleep(100)  # relay never makes progress

    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=side_effect)
    relay.touch_session("sess-timeout")
    for n in range(1, 8):
        session_queues["sess-timeout"].append({"n": n})  # relay is sitting at local cursor 7

    result = await relay.wait_until_relayed("sess-timeout", backend_cursor=999, epoch="e1", timeout=0.1)

    assert result.outcome == "timeout"
    assert result.local_cursor == 7

    await relay.stop()


@pytest.mark.asyncio
async def test_checkpoints_pruned_when_idle_but_retained_for_pending_waiter():
    """Old checkpoints are dropped once idle (past the grace window), except the
    single latest, and except one still needed to straddle a pending waiter's
    requested backend_cursor even if it's past the grace window."""
    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=lambda *a, **k: None)
    relay._session_queues["sess-prune"] = EventQueue()
    queue = relay._session_queues["sess-prune"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._CHECKPOINT_GRACE_SECONDS", 0.05)

        for n in range(1, 4):
            queue.append({"n": n})
            relay._record_checkpoint(
                "sess-prune",
                _Checkpoint(
                    epoch="e1", backend_cursor_before=n - 1, backend_cursor_after=n,
                    local_cursor_before=n - 1, local_cursor_after=n, recorded_at=time.monotonic(),
                ),
            )

        # Register a pending waiter interested in the OLDEST checkpoint's range
        # before it ages out, so pruning must keep it alive past the grace window.
        relay._pending_waiter_cursors["sess-prune"] = [0]

        await asyncio.sleep(0.1)  # let all 3 checkpoints age past the grace window
        relay._prune_checkpoints("sess-prune")

        kept = relay._checkpoints["sess-prune"]
        assert any(cp.backend_cursor_before == 0 for cp in kept)  # retained for the waiter
        assert kept[-1].backend_cursor_after == 3  # latest always retained

        relay._pending_waiter_cursors["sess-prune"] = []
        relay._prune_checkpoints("sess-prune")
        kept_after = relay._checkpoints["sess-prune"]
        assert len(kept_after) == 1
        assert kept_after[0].backend_cursor_after == 3  # only the latest survives once idle

    await relay.stop()


@pytest.mark.asyncio
async def test_checkpoints_retained_for_every_pending_waiter_not_just_the_minimum():
    """Regression test (epic #1990 stage 5a review finding): pruning must protect
    EVERY pending waiter's requested cursor, not just the smallest one — multiple
    concurrent GET /messages calls for the same session can register waiters at
    very different backend_cursor values. Before this fix, only the checkpoint
    covering min(pending) (plus the single latest) survived, silently dropping a
    non-minimum, non-latest waiter's checkpoint and corrupting its eventual
    interpolation (it would fall through to the frontier's "current position"
    instead, skipping everything in between)."""
    relay, backend_client, _, session_queues = _make_relay(request_json_side_effect=lambda *a, **k: None)
    relay._session_queues["sess-prune-multi"] = EventQueue()
    queue = relay._session_queues["sess-prune-multi"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._CHECKPOINT_GRACE_SECONDS", 0.05)

        for n in range(1, 5):
            queue.append({"n": n})
            relay._record_checkpoint(
                "sess-prune-multi",
                _Checkpoint(
                    epoch="e1", backend_cursor_before=n - 1, backend_cursor_after=n,
                    local_cursor_before=n - 1, local_cursor_after=n, recorded_at=time.monotonic(),
                ),
            )

        # Two concurrent waiters: one at the minimum (0), one at a MIDDLE cursor (2)
        # that is neither the minimum nor covered by the single latest checkpoint.
        relay._pending_waiter_cursors["sess-prune-multi"] = [0, 2]

        await asyncio.sleep(0.1)  # let all 4 checkpoints age past the grace window
        relay._prune_checkpoints("sess-prune-multi")

        kept = relay._checkpoints["sess-prune-multi"]
        assert any(cp.backend_cursor_before <= 0 <= cp.backend_cursor_after for cp in kept), (
            "checkpoint for the minimum pending waiter (0) was pruned"
        )
        assert any(cp.backend_cursor_before <= 2 <= cp.backend_cursor_after for cp in kept), (
            "checkpoint for the non-minimum pending waiter (2) was pruned"
        )
        assert relay._resolve_checkpoint("sess-prune-multi", "e1", 2) == 2

    await relay.stop()


# --- issue #2305: age-out / re-entry must not replay Backend's queue ----------------


async def _keep_alive(relay, session_id, stop):
    while not stop.is_set():
        relay.touch_session(session_id)
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_issue_2305_seed_backend_cursor_is_honored_and_sent_as_plain_since():
    seen = []

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        if "sess-seed" in sessions_req:
            seen.append(dict(sessions_req["sess-seed"]))
        await asyncio.sleep(0.01)
        return _batch(ui=_stream(), sessions={
            sid: _stream(next_cursor=q["since"], epoch="e1") for sid, q in sessions_req.items()
        })

    relay, *_ = _make_relay(side_effect)
    relay.touch_session("sess-seed", seed_backend_cursor=12)
    await _wait_until(lambda: seen)
    assert seen[0] == {"since": 12}
    await relay.stop()


@pytest.mark.asyncio
async def test_issue_2305_seed_arriving_during_in_flight_request_wins():
    release = asyncio.Event()
    requests = []

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        requests.append(dict(sessions_req.get("sess-race", {})))
        if requests[-1] == {"since": 0}:
            await release.wait()  # this answer would overwrite the seed if it landed
            return _batch(ui=_stream(), sessions={"sess-race": _stream(next_cursor=99, epoch="e1")})
        await asyncio.sleep(0.01)
        return _batch(ui=_stream(), sessions={
            sid: _stream(next_cursor=q["since"], epoch="e1") for sid, q in sessions_req.items()
        })

    relay, *_ = _make_relay(side_effect)
    relay.touch_session("sess-race")
    await _wait_until(lambda: {"since": 0} in requests)
    relay.touch_session("sess-race", seed_backend_cursor=7)
    await _wait_until(lambda: {"since": 7} in requests)
    release.set()
    await asyncio.sleep(0.05)
    assert relay._backend_cursor["sess-race"] == 7
    await relay.stop()


@pytest.mark.asyncio
async def test_issue_2305_reentry_resumes_from_cursor_logs_it_and_appends_only_new_events(caplog):
    requests = []
    backend_events = [{"n": i} for i in range(1, 6)]

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)
        if "sess-re" not in sessions_req:
            return _batch(ui=_stream(), sessions={})
        query = sessions_req["sess-re"]
        requests.append(dict(query))
        since = query["since"]
        head = len(backend_events)
        events = backend_events[since:]
        return _batch(ui=_stream(), sessions={"sess-re": _stream(events=events, next_cursor=head, epoch="e1")})

    del backend_events[3:]  # K=3 events before age-out
    relay, _, _, session_queues = _make_relay(side_effect)
    with pytest.MonkeyPatch.context() as mp, caplog.at_level(logging.INFO, logger="src.poll_relay"):
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.touch_session("sess-re", seed_backend_cursor=0)
        await _wait_until(lambda: session_queues["sess-re"].current_cursor == 3)
        await _wait_until(lambda: "sess-re" not in relay._session_last_activity, attempts=500)

        backend_events.extend([{"n": 4}, {"n": 5}])  # N=2 while aged out
        stop = asyncio.Event()
        keeper = asyncio.create_task(_keep_alive(relay, "sess-re", stop))
        await _wait_until(lambda: session_queues["sess-re"].current_cursor == 5)
        await asyncio.sleep(0.1)
        stop.set()
        await keeper

    assert {"since": 3} in requests
    assert session_queues["sess-re"].current_cursor == 5  # 3 + only the 2 new events
    events, _, _ = session_queues["sess-re"].events_since(0)
    assert [e["n"] for e in events] == [1, 2, 3, 4, 5]
    messages = [r.getMessage() for r in caplog.records]
    assert any("aged out" in m and "resume_cursor=3" in m for m in messages)
    assert any("re-entered subscription, resuming from backend cursor 3" in m for m in messages)
    await relay.stop()


@pytest.mark.asyncio
async def test_issue_2305_epoch_change_across_gap_emits_exactly_one_stream_reset():
    state = {"epoch": "e1"}

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)
        if "sess-ep" not in sessions_req:
            return _batch(ui=_stream(), sessions={})
        return _batch(ui=_stream(), sessions={
            "sess-ep": _stream(next_cursor=sessions_req["sess-ep"]["since"], epoch=state["epoch"])
        })

    relay, _, _, session_queues = _make_relay(side_effect)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.touch_session("sess-ep", seed_backend_cursor=0)
        await _wait_until(lambda: relay._last_epoch.get("sess-ep") == "e1")
        await _wait_until(lambda: "sess-ep" not in relay._session_last_activity, attempts=500)
        state["epoch"] = "e2"  # Backend restart / session reset during the gap
        stop = asyncio.Event()
        keeper = asyncio.create_task(_keep_alive(relay, "sess-ep", stop))
        await _wait_until(lambda: session_queues["sess-ep"].current_cursor >= 1)
        await asyncio.sleep(0.1)
        stop.set()
        await keeper

    events, _, _ = session_queues["sess-ep"].events_since(0)
    assert [e["type"] for e in events] == ["stream_reset"]
    await relay.stop()


@pytest.mark.asyncio
async def test_issue_2305_out_of_window_resume_delivers_events_with_evicted_flag():
    state = {"gap": False}

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)
        if "sess-ev" not in sessions_req:
            return _batch(ui=_stream(), sessions={})
        if state["gap"] and sessions_req["sess-ev"].get("since") == 2:
            state["gap"] = False
            return _batch(ui=_stream(), sessions={
                "sess-ev": _stream(events=[{"n": 8}, {"n": 9}], next_cursor=9, epoch="e1", evicted=True)
            })
        return _batch(ui=_stream(), sessions={
            "sess-ev": _stream(next_cursor=max(2, sessions_req["sess-ev"].get("since", 0)), epoch="e1")
        })

    relay, _, _, session_queues = _make_relay(side_effect)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", 0.05)
        relay.touch_session("sess-ev", seed_backend_cursor=2)
        await _wait_until(lambda: relay._backend_cursor.get("sess-ev") == 2 and "sess-ev" in relay._frontier)
        await _wait_until(lambda: "sess-ev" not in relay._session_last_activity, attempts=500)
        state["gap"] = True
        stop = asyncio.Event()
        keeper = asyncio.create_task(_keep_alive(relay, "sess-ev", stop))
        await _wait_until(lambda: session_queues["sess-ev"].current_cursor == 2)
        stop.set()
        await keeper

    assert relay.consume_pending_evicted("sess-ev") is True
    assert relay.consume_pending_evicted("sess-ev") is False  # consume-once
    await relay.stop()


@pytest.mark.asyncio
async def test_issue_2305_first_touch_since_zero_delivers_each_backend_event_once():
    """No remembered cursor + brand-new local queue + since=0: Backend is polled from 0
    exactly once per event (the local queue is empty, so nothing is duplicated)."""
    backend_events = [{"n": i} for i in range(1, 6)]

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)
        return _batch(ui=_stream(), sessions={
            sid: _stream(events=backend_events[q["since"]:], next_cursor=len(backend_events), epoch="e1")
            for sid, q in sessions_req.items()
        })

    relay, _, _, session_queues = _make_relay(side_effect)
    relay.touch_session("sess-first", since=0)
    await _wait_until(lambda: "sess-first" in session_queues and session_queues["sess-first"].current_cursor == 5)
    await asyncio.sleep(0.1)  # further polls must not re-append
    events, cursor, _ = session_queues["sess-first"].events_since(0)
    assert [e["n"] for e in events] == [1, 2, 3, 4, 5]
    assert cursor == 5
    await relay.stop()


@pytest.mark.asyncio
async def test_issue_2305_fresh_relay_with_browser_since_gt_zero_yields_single_stream_reset():
    """Relay restart (fresh relay, Backend still retaining events), browser arrives with a
    pre-restart cursor: exactly one stream_reset leads the local queue, never a second one."""
    backend_events = [{"type": "message", "n": i} for i in range(1, 4)]

    async def side_effect(*args, **kwargs):
        sessions_req = kwargs["json"]["sessions"]
        await asyncio.sleep(0.01)
        return _batch(ui=_stream(), sessions={
            sid: _stream(events=backend_events[q["since"]:], next_cursor=3, epoch="e1")
            for sid, q in sessions_req.items()
        })

    relay, _, _, session_queues = _make_relay(side_effect)
    relay.touch_session("sess-fresh", since=250)
    await _wait_until(lambda: session_queues["sess-fresh"].current_cursor == 4)
    await asyncio.sleep(0.1)
    events, _, _ = session_queues["sess-fresh"].events_since(0)
    assert [e["type"] for e in events] == ["stream_reset", "message", "message", "message"]
    await relay.stop()
