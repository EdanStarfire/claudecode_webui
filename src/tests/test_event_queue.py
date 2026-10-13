"""Tests for shared/event_queue.py's auto-increment append() (epic #1990 stage 5a).

Cursor-adoption support (`append(event, cursor=...)`) was removed in stage 5a — every
queue instance on both tiers now uses the same plain auto-increment discipline;
a Backend restart is surfaced via an explicit `stream_reset` event
(src/poll_relay.py) rather than inferred from cursor arithmetic (see the now-deleted
predecessor of this file for that history, issue #1886/#1889/#1890).
"""

from shared.event_queue import EventQueue


def test_append_auto_increments_cursor():
    queue = EventQueue()
    assert queue.append({"n": 1}) == 1
    assert queue.append({"n": 2}) == 2
    assert queue.append({"n": 3}) == 3

    events, next_cursor, _ = queue.events_since(0)
    assert [e["n"] for e in events] == [1, 2, 3]
    assert next_cursor == 3


def test_events_since_slices_from_requested_cursor():
    queue = EventQueue()
    for n in range(1, 6):
        queue.append({"n": n})

    events, next_cursor, evicted = queue.events_since(3)
    assert [e["n"] for e in events] == [4, 5]
    assert next_cursor == 5
    assert evicted is False


def test_events_since_on_empty_queue():
    queue = EventQueue()
    events, next_cursor, evicted = queue.events_since(0)
    assert events == []
    assert next_cursor == 0
    assert evicted is False


def test_eviction_past_max_size_advances_oldest_cursor():
    queue = EventQueue()
    queue.MAX_SIZE = 3
    for n in range(1, 6):
        queue.append({"n": n})

    events, next_cursor, evicted = queue.events_since(0)
    assert [e["n"] for e in events] == [3, 4, 5]  # 1, 2 evicted
    assert next_cursor == 5
    assert evicted is True
