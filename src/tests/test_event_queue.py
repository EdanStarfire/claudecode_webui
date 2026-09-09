"""Tests for shared/event_queue.py's explicit-cursor append() support (issue #1886).

Frontend's local poll_relay-fed EventQueue must adopt Backend's real per-event
cursor numbering instead of generating its own independent one, so a
Backend-space `event_cursor` (as returned by GET /api/sessions/{id}/messages)
resolves correctly against the local queue's events_since(). See plan-1886.md
for the full root-cause writeup.
"""

from shared.event_queue import EventQueue


def test_explicit_cursor_contiguous_append_behaves_like_auto_increment():
    queue = EventQueue()
    assert queue.append({"n": 1}, cursor=1) == 1
    assert queue.append({"n": 2}, cursor=2) == 2
    assert queue.append({"n": 3}, cursor=3) == 3

    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == [1, 2, 3]
    assert next_cursor == 3


def test_explicit_cursor_exact_redelivery_is_a_noop():
    queue = EventQueue()
    queue.append({"n": 1}, cursor=1)
    queue.append({"n": 2}, cursor=2)

    result = queue.append({"n": "duplicate-of-2"}, cursor=2)

    assert result == 2
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == [1, 2]  # not duplicated, not replaced
    assert next_cursor == 2


def test_explicit_cursor_forward_gap_drops_stale_history_and_anchors_fresh():
    """Simulates Backend evicting events past the relay's last known position:
    the next batch arrives with a cursor that skips ahead of self._cursor + 1."""
    queue = EventQueue()
    queue.append({"n": 1}, cursor=1)
    queue.append({"n": 2}, cursor=2)

    result = queue.append({"n": "gap"}, cursor=50)

    assert result == 50
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == ["gap"]
    assert next_cursor == 50

    # An old `since` value now legitimately falls into the "too old, here's
    # everything we currently have" fallback rather than mis-slicing.
    events, next_cursor = queue.events_since(1)
    assert [e["n"] for e in events] == ["gap"]
    assert next_cursor == 50


def test_explicit_cursor_backward_jump_is_treated_as_reset_not_silent_drop():
    """Simulates a Backend process restart: backend/web_server.py recreates a
    fresh EventQueue() (cursor 0) for every session on startup, so post-restart
    events arrive at the relay carrying LOWER cursor numbers than the local
    queue's already-consumed range. The new event must not be silently dropped
    as a false-duplicate."""
    queue = EventQueue()
    queue.append({"n": "pre-restart-a"}, cursor=98)
    queue.append({"n": "pre-restart-b"}, cursor=99)
    queue.append({"n": "pre-restart-c"}, cursor=100)

    result = queue.append({"n": "post-restart-1"}, cursor=1)

    assert result == 1
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == ["post-restart-1"]
    assert next_cursor == 1

    # The next post-restart event continues to append contiguously as normal.
    queue.append({"n": "post-restart-2"}, cursor=2)
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == ["post-restart-1", "post-restart-2"]
    assert next_cursor == 2


def test_explicit_cursor_backward_jump_self_heals_for_a_stale_high_since():
    """The specific production trigger for the backward-jump/reset branch: a
    browser tab (or an in-flight long-poll waiter) is holding the OLD,
    pre-restart high `since` value at the moment of reset. events_since() must
    not error or mis-slice — it reports "nothing yet" at the new, lower
    current_cursor, which is exactly the signal src/routers/poll.py's callers
    (frontend/src/stores/polling.js unconditionally adopts next_cursor) rely on
    to self-correct onto the new cursor space rather than polling with the
    stale value forever."""
    queue = EventQueue()
    for n in range(98, 101):
        queue.append({"n": n}, cursor=n)
    assert queue.current_cursor == 100

    queue.append({"n": "post-restart-1"}, cursor=1)

    events, next_cursor = queue.events_since(100)
    assert events == []
    assert next_cursor == 1  # lower than the stale `since` passed in — the self-heal signal

    # Once the caller adopts that lower cursor, subsequent events resolve normally.
    queue.append({"n": "post-restart-2"}, cursor=2)
    events, next_cursor = queue.events_since(1)
    assert [e["n"] for e in events] == ["post-restart-2"]
    assert next_cursor == 2


def test_explicit_cursor_first_ever_append_anchors_oldest_cursor():
    """A relay-fed queue's very first append may start at any Backend cursor
    value (e.g. a session whose relay starts well after session creation) —
    oldest_cursor must anchor there, not stay at the class default of 1."""
    queue = EventQueue()

    queue.append({"n": "first"}, cursor=42)

    events, next_cursor = queue.events_since(41)
    assert [e["n"] for e in events] == ["first"]
    assert next_cursor == 42

    # since < oldest_cursor - 1 falls into the "too old" fallback correctly.
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == ["first"]
    assert next_cursor == 42


def test_default_no_cursor_append_path_is_unchanged():
    queue = EventQueue()
    assert queue.append({"n": 1}) == 1
    assert queue.append({"n": 2}) == 2
    events, next_cursor = queue.events_since(0)
    assert [e["n"] for e in events] == [1, 2]
    assert next_cursor == 2
