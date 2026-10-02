"""The single writer to every EventQueue (issue #2063 AC2). Validates against
shared/event_registry.py; see module docstring in event_registry.py for the registry
shape. `STRICT` governs whether a registry violation raises or just logs — production
always runs lenient (a registry mistake must never silently drop a user-visible event,
AC3), tests toggle it per-test via `strict_mode()` to exercise T1's "reject in test
mode" half without that becoming the global pytest default (the same test module needs
both halves of T1 to run as actual, non-mocked calls)."""

import logging
from contextlib import contextmanager

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
        missing = spec.required_keys - payload.keys()
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
