"""The single writer to every EventQueue (issue #2063 AC2). Validates against
shared/event_registry.py; see module docstring in event_registry.py for the registry
shape. `STRICT` governs whether a registry violation raises or just logs — production
always runs lenient (a registry mistake must never silently drop a user-visible event,
AC3), tests toggle it per-test via `strict_mode()` to exercise T1's "reject in test
mode" half without that becoming the global pytest default (the same test module needs
both halves of T1 to run as actual, non-mocked calls)."""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from shared.event_envelope import QUEUE_SESSION
from shared.event_queue import EventQueue
from shared.event_registry import TOP_LEVEL_EVENT_TYPES

logger = logging.getLogger(__name__)

STRICT = False


class EventRegistryViolationError(Exception):
    pass


@contextmanager
def strict_mode():
    global STRICT
    previous, STRICT = STRICT, True
    try:
        yield
    finally:
        STRICT = previous


def _validate(family: str, event_type: str, payload: dict) -> None:
    spec = TOP_LEVEL_EVENT_TYPES.get(event_type)
    problems = []
    if spec is None:
        problems.append(f"unregistered event type {event_type!r}")
    else:
        if family not in spec.queues:
            problems.append(
                f"event type {event_type!r} not valid on queue family {family!r} "
                f"(registered for {sorted(spec.queues)})"
            )
        missing = spec.required_keys_for(family) - payload.keys()
        if missing:
            problems.append(f"event type {event_type!r} missing required key(s): {sorted(missing)}")

    if problems:
        message = "; ".join(problems)
        if STRICT:
            raise EventRegistryViolationError(message)
        logger.error("emit() registry violation (continuing): %s", message)


def emit(queue: EventQueue, family: str, event_type: str, payload: dict) -> int:
    """Validates then appends `{"type": event_type, **payload}` — the exact shape every
    call site already builds today. `family` is one of `event_envelope.QUEUE_UI` /
    `QUEUE_SESSION` / `QUEUE_AUDIT`, identifying which queue `queue` actually is (needed
    for the registry's queue-family check; an `EventQueue` instance carries no family of
    its own)."""
    _validate(family, event_type, payload)
    return queue.append({"type": event_type, **payload})


# ---------------------------------------------------------------------------
# ISSUE #2063 AC5 SHIM. Stage 2b (#2065) deletes this function and replaces its
# 7 call sites with a single emit() of the canonical bare shape, once the browser
# dispatcher no longer needs the legacy wrapped form.
# ---------------------------------------------------------------------------
def emit_tool_call(queue: EventQueue, session_id: str, tool_call_data: dict) -> None:
    """Emits one logical tool_call fact as BOTH shapes the browser currently
    dispatches: the canonical bare `tool_call` envelope, and the legacy
    `message`-wrapped form `polling.js`'s dispatcher still reads directly
    (not routed through this shim). Both emissions share one timestamp —
    generated once, here — so they describe the identical instant rather than
    two independently-stamped moments.
    """
    tool_call_data.setdefault("type", "tool_call")
    timestamp = datetime.now(UTC).isoformat()
    emit(queue, QUEUE_SESSION, "tool_call", {
        "session_id": session_id,
        "data": tool_call_data,
        "timestamp": timestamp,
    })
    # A fresh copy, not the same `tool_call_data` object as above — so the two queued
    # entries don't alias one mutable dict (a later in-place mutation of one must not
    # silently corrupt the other, already-queued, entry).
    emit(queue, QUEUE_SESSION, "message", {
        "session_id": session_id,
        "data": dict(tool_call_data),
        "timestamp": timestamp,
    })
