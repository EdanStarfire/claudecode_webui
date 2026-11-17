"""Startup recovery of the pending delivery ledger (issue #2362).

``PendingStore.load()`` rebuilds a session's ledger from ``pending.jsonl``; this module then decides
what may still be delivered after a Backend restart. The store stays a pure data layer and the core
stays relay-agnostic: producers that need special handling (relay monitor envelopes, instruction
TTLs) plug in through the two optional callbacks.

Rules, applied per ``pending`` item in this order:
  1. not ``WHEN_IDLE`` (IMMEDIATE / HALT / PIVOT are only meaningful live)  -> ``failed``
  2. older than its TTL                                                      -> ``failed`` ("expired")
  3. ``classify`` returns ``"cancel"``                                       -> ``cancelled``
Everything else stays pending and is replayed (at-least-once: a crash between the send and
``mark_delivered`` re-sends once).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .models.legion_models import DeliveryPolicy, SenderType
from .pending_store import PendingItem, PendingStore

PENDING_TTL_SECONDS = 24 * 3600
PENDING_TTL_SCHEDULE_SECONDS = 3600
# Boot drain pacing: sessions started at once / pause between session starts.
BOOT_DRAIN_BATCH = 3
BOOT_DRAIN_SPACING_SECONDS = 2.0

ERROR_NOT_REPLAYED = "backend restarted before delivery; not replayed"
ERROR_EXPIRED = "expired"


@dataclass
class RecoveryReport:
    """Per-session outcome of one recovery pass (items are the post-transition ledger entries)."""
    session_id: str
    kept: list[PendingItem] = field(default_factory=list)
    failed_not_replayed: list[PendingItem] = field(default_factory=list)
    expired: list[PendingItem] = field(default_factory=list)
    cancelled: list[PendingItem] = field(default_factory=list)


def ttl_seconds(item: PendingItem) -> float:
    return (
        PENDING_TTL_SCHEDULE_SECONDS if item.comm.sender_type == SenderType.SCHEDULE
        else PENDING_TTL_SECONDS
    )


async def recover_session(
    store: PendingStore,
    session_id: str,
    session_dir: Path,
    *,
    now: float,
    classify: Callable[[PendingItem], str | None] | None = None,
    ttl_override: Callable[[PendingItem], float | None] | None = None,
) -> RecoveryReport:
    """Load ``session_id``'s ledger and apply the recovery rules. ``ttl_override`` returns a TTL in
    seconds for items that need a shorter/longer one than the sender default (or None)."""
    await store.load(session_id, session_dir)
    report = RecoveryReport(session_id)
    for item in store.get_pending(session_id):
        comm_id = item.comm.comm_id
        if item.comm.delivery != DeliveryPolicy.WHEN_IDLE:
            done = await store.mark_failed(session_id, session_dir, comm_id, ERROR_NOT_REPLAYED)
            if done:
                report.failed_not_replayed.append(done)
            continue
        ttl = (ttl_override(item) if ttl_override else None) or ttl_seconds(item)
        if now - item.created_at > ttl:
            done = await store.mark_failed(session_id, session_dir, comm_id, ERROR_EXPIRED)
            if done:
                report.expired.append(done)
            continue
        if classify and classify(item) == "cancel":
            done = await store.cancel(session_id, session_dir, comm_id)
            if done:
                report.cancelled.append(done)
            continue
        report.kept.append(item)
    return report
