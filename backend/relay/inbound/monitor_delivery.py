"""Comm shape for monitored content and the coordinator-backed delivery port (issue #2103 3b-i).

Monitored content is an INFO comm from the system (``from_minion_id=SYSTEM_MINION_ID``,
``from_user=False``) tagged ``sender_type=EXTERNAL`` / ``sender_id=relay_id``, delivered
``WHEN_IDLE`` with ``auto_start`` (it exists only when there IS content). Consequences, each pinned
in tests: ``DeliveryGate._same_sender`` never matches it with a user's PIVOT/HALT (those cancel a
pending 3a instruction, which is a user comm, but not pending monitor content); ``SYSTEM_MINION_ID``
is never an ancestor so it can never HALT/PIVOT or implicitly resume a paused queue; it is never
addressed to a parent / overseer (the recipient is the monitoring session only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.legion_models import (
    SYSTEM_MINION_ID,
    Comm,
    CommType,
    DeliveryPolicy,
    SenderType,
)

from .service import DeliveryOutcome

if TYPE_CHECKING:
    from backend.session_coordinator import SessionCoordinator

MONITOR_SENDER_NAME = "Slack monitor"


async def enqueue_monitor_comm(
    coordinator: SessionCoordinator,
    session_id: str,
    *,
    comm_id: str,
    summary: str,
    content: str,
    dispatch_content: str,
    metadata: dict,
    relay_id: str,
) -> DeliveryOutcome:
    """Deliver one monitor envelope through ``DeliveryGate.deliver``. ``content`` is the counts-only
    ledger text; ``dispatch_content`` (the fenced envelope) is what the session receives."""
    existing = coordinator.pending_store.get_item(session_id, comm_id)
    if existing is not None and existing.status == "pending":
        return DeliveryOutcome("already_delivered")  # the same envelope is already queued
    comm = Comm(
        comm_id=comm_id, from_minion_id=SYSTEM_MINION_ID, from_user=False,
        from_minion_name=MONITOR_SENDER_NAME, sender_type=SenderType.EXTERNAL, sender_id=relay_id,
        to_minion_id=session_id, summary=summary, content=content, comm_type=CommType.INFO,
        delivery=DeliveryPolicy.WHEN_IDLE, reset_first=False, auto_start=True, metadata=metadata,
    )
    result = await coordinator.delivery_gate.deliver(
        comm, dispatch_content=dispatch_content, dispatch_metadata=metadata, inject_timestamp=False,
    )
    if coordinator.pending_store.get_item(session_id, comm_id) is None:
        return DeliveryOutcome("failed", result.error or "not enqueued")
    return DeliveryOutcome("delivered")


class CoordinatorPort:
    """What the inbound service needs from the delivery ledger (ports keep tests simple)."""

    def __init__(self, coordinator: SessionCoordinator) -> None:
        self._coordinator = coordinator

    async def enqueue(self, session_id: str, **kwargs) -> DeliveryOutcome:
        return await enqueue_monitor_comm(self._coordinator, session_id, **kwargs)

    def item_status(self, session_id: str, comm_id: str) -> str | None:
        item = self._coordinator.pending_store.get_item(session_id, comm_id)
        return item.status if item is not None else None

    def pending_monitor_count(self, session_id: str, relay_id: str) -> int:
        """Monitor envelopes of this relay that are queued and not yet dispatched."""
        return sum(
            1 for item in self._coordinator.pending_store.get_pending(session_id)
            if ((item.comm.metadata or {}).get("relay_inbound") or {}).get("kind") == "monitor"
            and item.comm.sender_id == relay_id
        )

    async def cancel(self, session_id: str, comm_id: str) -> bool:
        """Cancel a still-pending envelope (content from a blocked channel / removed monitor must
        not reach the session). The ledger status becomes ``cancelled`` (pending-only guard)."""
        coordinator = self._coordinator
        session_dir = await coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return False
        return await coordinator.pending_store.cancel(session_id, session_dir, comm_id) is not None
