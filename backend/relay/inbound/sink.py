"""Delivery sink: an owner's Slack instruction enters the unified delivery gate (issue #2103 3a)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.legion_models import Comm, CommType, DeliveryPolicy, SenderType

from .service import DeliveryOutcome

if TYPE_CHECKING:
    from backend.session_coordinator import SessionCoordinator


async def enqueue_external_comm(
    coordinator: SessionCoordinator,
    session_id: str,
    *,
    comm_id: str,
    summary: str,
    content: str,
    dispatch_content: str,
    metadata: dict | None = None,
    relay_id: str | None = None,
) -> DeliveryOutcome:
    """Deliver an external (Slack self-DM) instruction through ``DeliveryGate.deliver``.

    ``comm_id`` is deterministic per Slack message; a comm the in-memory pending store already
    holds is not enqueued twice. The pending ledger is NOT loaded here (the queue is not recovered
    across a Backend restart, issue #2362). ``from_user=True`` because ``Comm.validate`` needs a
    user or minion source (the owner's own instruction); ``sender_type=EXTERNAL`` records how it
    arrived. ``CommType.SYSTEM`` like ``enqueue_message()``: never an implicit resume trigger, so
    a paused queue keeps the instruction pending (issue #2193 AC6).
    """
    if coordinator.pending_store.get_item(session_id, comm_id) is not None:
        return DeliveryOutcome("already_delivered")
    comm = Comm(
        comm_id=comm_id, from_user=True, sender_type=SenderType.EXTERNAL, sender_id=relay_id,
        to_minion_id=session_id, summary=summary, content=content, comm_type=CommType.SYSTEM,
        delivery=DeliveryPolicy.WHEN_IDLE, reset_first=False, auto_start=True,
        metadata=metadata or {},
    )
    result = await coordinator.delivery_gate.deliver(
        comm, dispatch_content=dispatch_content, dispatch_metadata=metadata or {},
        inject_timestamp=False,
    )
    if coordinator.pending_store.get_item(session_id, comm_id) is None:
        return DeliveryOutcome("failed", result.error or "not enqueued")
    return DeliveryOutcome("delivered")
