"""Tests for shared/poll_multiplex.py (epic #1990 stage 5c).

Covers the two corrected design points this helper is specifically responsible
for getting right:
1. `evicted` reflects the UNTRUNCATED events_since() result; `reset` reflects
   only the TRUNCATED/actually-delivered slice.
2. Quota truncation keeps `next_cursor` resumable (no gap, no duplicate replay).

Plus the raced-wait behavior: one quiet stream never blocks another's prompt
delivery.
"""

import asyncio

import pytest

from shared.event_queue import EventQueue
from shared.poll_multiplex import DEFAULT_EVENTS_PER_STREAM_QUOTA, multiplex_poll


def _make_queue_with_events(n: int, type_at: dict[int, str] | None = None) -> EventQueue:
    """Append n events (1-indexed cursor), optionally overriding event["type"] at
    specific 1-indexed positions (e.g. to inject a stream_reset marker)."""
    type_at = type_at or {}
    queue = EventQueue()
    for i in range(1, n + 1):
        queue.append({"type": type_at.get(i, "message"), "i": i})
    return queue


@pytest.mark.asyncio
async def test_empty_streams_returns_empty_dict():
    assert await multiplex_poll({}, timeout=1.0) == {}


@pytest.mark.asyncio
async def test_single_stream_no_truncation_returns_all_events():
    queue = _make_queue_with_events(3)
    results = await multiplex_poll({"ui": (queue, 0)}, timeout=1.0)
    result = results["ui"]
    assert [e["i"] for e in result.events] == [1, 2, 3]
    assert result.next_cursor == 3
    assert result.evicted is False
    assert result.reset is False


@pytest.mark.asyncio
async def test_quota_truncation_keeps_next_cursor_resumable():
    """next_cursor after truncation must be exactly the cursor of the last
    delivered event — resuming from it must replay nothing and skip nothing."""
    queue = _make_queue_with_events(10)
    results = await multiplex_poll({"s1": (queue, 0)}, timeout=1.0, quota_per_stream=4)
    result = results["s1"]
    assert [e["i"] for e in result.events] == [1, 2, 3, 4]
    assert result.next_cursor == 4

    # Resuming from the truncated next_cursor delivers exactly the remainder,
    # with no gap and no re-delivery of already-seen events.
    results2 = await multiplex_poll({"s1": (queue, result.next_cursor)}, timeout=1.0, quota_per_stream=4)
    result2 = results2["s1"]
    assert [e["i"] for e in result2.events] == [5, 6, 7, 8]
    assert result2.next_cursor == 8


@pytest.mark.asyncio
async def test_evicted_computed_from_untruncated_result_even_when_truncated():
    """A too-old cursor must be reported evicted=True regardless of whether the
    full (evicted-recovery) event list also gets truncated by quota."""
    queue = EventQueue()
    queue.MAX_SIZE = 3
    for i in range(5):
        queue.append({"type": "message", "i": i})
    # Oldest retained cursor is 3; cursor=0 is evicted.
    results = await multiplex_poll({"s1": (queue, 0)}, timeout=1.0, quota_per_stream=1)
    result = results["s1"]
    assert result.evicted is True
    # Still truncated to quota despite being the evicted-recovery snapshot.
    assert len(result.events) == 1


@pytest.mark.asyncio
async def test_reset_marker_beyond_truncation_cutoff_not_reported_yet():
    """The core corrected-design assertion: a stream_reset marker sitting beyond
    the truncation cutoff must NOT cause reset=True on a response whose events
    array doesn't contain it. It arrives untouched, with reset=True, once the
    cursor catches up to it."""
    # Event 5 is the stream_reset marker; quota=3 means the response under test
    # only ever sees events 1-3 (quota from cursor=0) or similar prefix.
    queue = _make_queue_with_events(6, type_at={5: "stream_reset"})

    results = await multiplex_poll({"s1": (queue, 0)}, timeout=1.0, quota_per_stream=3)
    result = results["s1"]
    assert [e["i"] for e in result.events] == [1, 2, 3]
    assert result.reset is False  # marker (event 5) is beyond this truncated slice
    assert result.next_cursor == 3

    # Catching up: next poll's truncated slice now contains the marker, and reset
    # flips to True in that exact response.
    results2 = await multiplex_poll({"s1": (queue, 3)}, timeout=1.0, quota_per_stream=3)
    result2 = results2["s1"]
    assert [e["i"] for e in result2.events] == [4, 5, 6]
    assert result2.reset is True
    assert result2.next_cursor == 6


@pytest.mark.asyncio
async def test_reset_marker_within_truncated_slice_reported_immediately():
    queue = _make_queue_with_events(4, type_at={2: "stream_reset"})
    results = await multiplex_poll({"s1": (queue, 0)}, timeout=1.0, quota_per_stream=10)
    result = results["s1"]
    assert result.reset is True
    assert [e["i"] for e in result.events] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_default_quota_constant_used_when_unspecified():
    queue = _make_queue_with_events(5)
    results = await multiplex_poll({"ui": (queue, 0)}, timeout=1.0)
    result = results["ui"]
    assert len(result.events) <= DEFAULT_EVENTS_PER_STREAM_QUOTA
    assert len(result.events) == 5  # well under the default quota


@pytest.mark.asyncio
async def test_quiet_stream_does_not_block_active_stream():
    """One stream with no new data must not delay another stream's prompt
    delivery — the whole point of racing wait_for_events() per stream."""
    quiet = EventQueue()
    active = EventQueue()

    async def append_soon():
        await asyncio.sleep(0.05)
        active.append({"type": "message"})

    task = asyncio.ensure_future(append_soon())
    try:
        results = await multiplex_poll(
            {"quiet": (quiet, 0), "active": (active, 0)}, timeout=5.0
        )
    finally:
        await task

    assert results["active"].events  # delivered promptly
    assert results["quiet"].events == []  # nothing to deliver, no error


@pytest.mark.asyncio
async def test_timeout_elapses_with_no_events_on_any_stream():
    queue = EventQueue()
    results = await multiplex_poll({"s1": (queue, 0)}, timeout=0.05)
    result = results["s1"]
    assert result.events == []
    assert result.next_cursor == 0
    assert result.evicted is False
    assert result.reset is False


@pytest.mark.asyncio
async def test_multiple_streams_assembled_independent_of_which_one_woke():
    """Once any stream wakes, every subscribed stream is assembled from whatever
    its queue currently holds — not just the one that triggered the wake."""
    ui_queue = _make_queue_with_events(2)
    session_queue = EventQueue()  # stays empty

    results = await multiplex_poll(
        {"ui": (ui_queue, 0), "s1": (session_queue, 0)}, timeout=0.1
    )
    assert len(results["ui"].events) == 2
    assert results["s1"].events == []
