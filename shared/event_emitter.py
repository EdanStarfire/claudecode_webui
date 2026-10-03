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

from shared.event_envelope import DEFAULT_BACKEND_ID, EventEnvelope, fold_payload
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


def emit(
    queue: EventQueue,
    family: str,
    event_type: str,
    payload: dict,
    *,
    scope: str | None = None,
    backend_id: str = DEFAULT_BACKEND_ID,
) -> int:
    """Validates then appends a real `EventEnvelope` built around `payload`. `family` is
    one of `event_envelope.QUEUE_UI` / `QUEUE_SESSION` / `QUEUE_AUDIT`, identifying which
    queue `queue` actually is (needed for the registry's queue-family check; an
    `EventQueue` instance carries no family of its own).

    The envelope's `sequence` is precomputed from `queue.current_cursor` before
    `append()` runs — not patched onto the dict afterward — because `EventQueue.append()`'s
    `on_append` hook fires synchronously *inside* `append()`, before it returns the new
    cursor, so mutating the dict after the fact would miss that hook entirely. Nothing
    awaits between reading the cursor and appending, so this is race-free for every
    `emit()` call site (all of which use the auto-increment discipline — cursor adoption
    belongs exclusively to poll_relay.py, which never calls `emit()`).

    `payload` is folded through the same `fold_payload()` `EventEnvelope.from_dict()`
    uses to read an already-appended dict back — roughly half of today's call sites
    already nest their fields under their own "data" key (today's ad-hoc convention),
    and folding on the way in too means `.data` ends up in the same shape either way,
    with no double-nesting for consumers reading through `from_dict()`.
    """
    _validate(family, event_type, payload)
    sequence = queue.current_cursor + 1
    envelope = EventEnvelope(
        type=event_type,
        queue=family,
        sequence=sequence,
        timestamp=datetime.now(UTC).isoformat(),
        data=fold_payload(payload),
        backend_id=backend_id,
        scope=scope,
    )
    return queue.append(envelope.to_dict())
