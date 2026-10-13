"""Race+quota+assembly helper for the multiplexed `POST /api/poll` endpoint
(epic #1990 stage 5c). Used identically by both tiers' routers against their own
`EventQueue` instances — Backend's real queues (`backend/routers/poll.py`) and the
Frontend tier's locally-relayed copies (`src/routers/poll.py`) — isolating the one
piece of logic flagged as highest-risk in a single shared, unit-tested place rather
than duplicating it per-tier. A truncation-cursor bug here silently drops events
rather than erroring, so this module is deliberately small and self-contained.
"""

import asyncio
from dataclasses import dataclass

from shared.event_queue import EventQueue

# Per-stream cap on events returned in one response — decouples "waiting for new
# data" (per-stream, raced below) from "capping response size" (per-stream, at
# assembly time), so one noisy stream can't starve the others' delivery even
# though every subscribed stream is assembled on the same response.
DEFAULT_EVENTS_PER_STREAM_QUOTA = 200


def _contains_stream_reset(events: list[dict]) -> bool:
    """Whether this exact (possibly truncated) batch contains a `stream_reset`
    marker. Must only ever be evaluated against what a response is actually
    returning — see `multiplex_poll()`'s docstring for why."""
    return any(e.get("type") == "stream_reset" for e in events)


@dataclass
class StreamResult:
    events: list[dict]
    next_cursor: int
    reset: bool
    evicted: bool


async def multiplex_poll(
    streams: dict[str, tuple[EventQueue, int]],
    timeout: float,
    quota_per_stream: int = DEFAULT_EVENTS_PER_STREAM_QUOTA,
) -> dict[str, StreamResult]:
    """Race `wait_for_events()` across every subscribed stream's queue, then assemble
    every stream's quota-truncated slice.

    `streams` maps stream_key ("ui" or a session_id) -> (queue, since_cursor).

    Waiting is raced (`asyncio.wait(..., return_when=FIRST_COMPLETED)`) so a slow/
    quiet stream's own `wait_for_events()` never blocks another stream's from
    resolving promptly — same long-poll responsiveness per stream as the single-
    stream endpoints this replaces. Once any stream wakes (or `timeout` elapses),
    every subscribed stream — not just the one that woke — is assembled from
    whatever its queue currently holds.

    `evicted` is computed from the UNTRUNCATED `events_since()` result — a property
    of the requested cursor vs. the queue's current retained window, independent of
    truncation. `reset` is computed from the TRUNCATED/actually-delivered slice
    only, via `_contains_stream_reset()` run against exactly what's being returned
    — so the field always accurately describes that specific response's own
    content. A `stream_reset` marker sitting beyond a truncation cutoff (possible
    for a long-unpolled, heavily-backlogged stream with a small quota) is simply
    not yet reflected in `reset`; it gets delivered — with an accurate `reset: true`
    alongside it — on a later poll once the cursor catches up. No data loss either
    way, just a semantic-consistency requirement: `reset` must never claim
    something `events` doesn't contain.
    """
    if not streams:
        return {}

    waits = [
        asyncio.ensure_future(queue.wait_for_events(cursor, timeout=timeout))
        for queue, cursor in streams.values()
    ]
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
    finally:
        for wait in waits:
            if not wait.done():
                wait.cancel()
        await asyncio.gather(*waits, return_exceptions=True)

    results: dict[str, StreamResult] = {}
    for stream_key, (queue, cursor) in streams.items():
        full_events, full_next_cursor, evicted = queue.events_since(cursor)
        # Every branch of events_since() returns a contiguous run of events ending
        # exactly at full_next_cursor, so the cursor immediately preceding the
        # first returned event is always full_next_cursor - len(full_events) —
        # whether that run started at the originally-requested `cursor` (the
        # common case) or at the queue's oldest retained event (evicted / reset
        # cases, where the full buffer is returned regardless of `cursor`).
        baseline_cursor = full_next_cursor - len(full_events)
        truncated_events = full_events[:quota_per_stream]
        if len(truncated_events) < len(full_events):
            next_cursor = baseline_cursor + len(truncated_events)
        else:
            next_cursor = full_next_cursor
        results[stream_key] = StreamResult(
            events=truncated_events,
            next_cursor=next_cursor,
            reset=_contains_stream_reset(truncated_events),
            evicted=evicted,
        )
    return results
