"""
DeliveryGate - Consolidated message-delivery readiness gating (issue #2189,
coordinator-split PR 10; groundwork for #2193).

Before this module existed, two independent "wait before delivering a
message" gates lived in two different files:

- comm_router.py's STARTING-only readiness wait / auto-start-via-
  is_deliverable() dance (SDK-event-driven, via wait_for_session_ready()).
- queue_processor.py's AUTO_START_STATES-gated auto-start + state-poll
  busy-wait, followed by a post-send idle busy-wait.

Both now live here, as two deliberately separate branches of one entry
point (deliver()). They are NOT unified: each branch preserves its own
auto-start predicate and its own readiness mechanism verbatim. Unifying
them would be a behavior change outside this PR's scope.
"""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from .models.legion_models import Comm
from .session_lifecycle import (
    AUTO_START_STATES,
    SessionState,
    is_active,
    is_deliverable,
    is_errored,
    is_paused,
)

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

gate_logger = get_logger('delivery_gate', category='DELIVERY_GATE')

# Default timing configuration (verbatim from queue_processor.py).
DEFAULT_MIN_WAIT_SECONDS = 10
DEFAULT_MIN_IDLE_SECONDS = 10


@dataclass
class DeliveryResult:
    """success=False with error=None is a deliberate, narrow case (the
    wait_for_idle=True post-reset-wait-timeout path): leave the item pending
    rather than marking it permanently failed — a verbatim-preserved asymmetry
    from pre-PR queue_processor.py. Every other failure sets a non-None error.
    """
    success: bool
    error: str | None = None


class DeliveryGate:
    """Single entry point for delivering a Comm to a minion's SDK session.

    deliver() branches on wait_for_idle to pick which of the two readiness
    mechanisms above to run — see module docstring.

    The wait_for_idle=False mechanics are also exposed as two split primitives,
    ensure_ready() and send(), rather than only through deliver(). Pre-PR,
    comm_router.py's auto-start/readiness-wait gate ran unconditionally for
    every comm BEFORE its HALT/PIVOT interrupt-priority branching — a HALT
    comm to a dormant target still forced it through the gate (auto-started,
    then immediately interrupted); a PIVOT comm ran the gate, then interrupted,
    then sent. Collapsing gate+send into one atomic deliver() call would force
    comm_router.py to call it AFTER its interrupt-priority branch (since HALT
    must skip the send but PIVOT/NORMAL must not), silently reordering the
    gate to run after the interrupt instead of before it — a real behavior
    change, not just an internal-mechanics detail. ensure_ready() lets
    comm_router.py run the gate first, unconditionally, exactly as before,
    then decide whether to call send() afterward based on interrupt priority.
    """

    def __init__(self, coordinator: "SessionCoordinator"):
        self._coordinator = coordinator

    async def deliver(
        self,
        comm: Comm,
        *,
        wait_for_idle: bool,
        reset_first: bool = False,
        inject_timestamp: bool = True,
        readiness_timeout: float | None = None,
    ) -> DeliveryResult:
        if wait_for_idle:
            return await self._deliver_with_busy_wait(
                comm, reset_first=reset_first, inject_timestamp=inject_timestamp
            )
        ready = await self.ensure_ready(comm, readiness_timeout=readiness_timeout)
        if not ready.success:
            return ready
        return await self.send(comm, inject_timestamp=inject_timestamp)

    # =========================================================================
    # wait_for_idle=False — ported verbatim from comm_router.py::_send_to_minion
    # =========================================================================

    async def ensure_ready(
        self,
        comm: Comm,
        *,
        readiness_timeout: float | None = None,
    ) -> DeliveryResult:
        """Target-check + STARTING-wait / auto-start-via-is_deliverable() gate.

        No send — call send() separately once this succeeds. Call this
        unconditionally, before any interrupt-priority branching (see class
        docstring for why).
        """
        coordinator = self._coordinator
        session_id = comm.to_minion_id

        target_minion = await coordinator.session_manager.get_session_info(session_id)
        if not target_minion:
            gate_logger.error(f"Target minion {session_id} not found")
            return DeliveryResult(success=False, error="Target minion not found")

        if target_minion.state == SessionState.STARTING:
            # Session is starting but SDK may not be ready yet — wait for readiness
            gate_logger.info(
                f"Target minion {session_id} is STARTING - waiting for SDK readiness"
            )
            timeout = readiness_timeout if readiness_timeout is not None else 60.0
            ready = await coordinator.wait_for_session_ready(session_id, timeout=timeout)
            if not ready:
                gate_logger.error(
                    f"Minion {session_id} did not become ready within timeout"
                )
                return DeliveryResult(
                    success=False,
                    error="Minion did not become ready within 60 seconds",
                )
        elif not is_deliverable(target_minion.state):
            gate_logger.info(f"Target minion {session_id} is in {target_minion.state} state - auto-starting")

            # Register WebSocket message callback before starting
            # This ensures messages from the auto-started minion broadcast to WebSocket clients
            if coordinator._message_callback_registrar:
                coordinator._message_callback_registrar(session_id)
                gate_logger.info(f"Registered WebSocket message callback for auto-started minion {session_id}")
            else:
                gate_logger.warning(f"No message callback registrar available for auto-start of {session_id}")

            # Start the session with permission callback
            # Create permission callback using factory (same pattern as user-created minions)
            permission_callback = None
            if coordinator._permission_callback_factory:
                permission_callback = coordinator._permission_callback_factory(session_id)
                gate_logger.info(f"Created permission callback for auto-started minion {session_id}")
            else:
                gate_logger.warning(f"No permission callback factory available for auto-start of {session_id}")

            success = await coordinator.start_session(
                session_id=session_id,
                permission_callback=permission_callback
            )

            if not success:
                reason = None
                try:
                    failed_info = await coordinator.session_manager.get_session_info(session_id)
                    reason = failed_info.error_message if failed_info else None
                except Exception:
                    gate_logger.exception(
                        f"Failed to retrieve failure reason for minion {session_id}"
                    )

                detail = reason or f"state: {target_minion.state}"
                gate_logger.error(
                    f"Failed to auto-start minion {session_id}: {detail}"
                )
                comm.metadata["delivery_failure_reason"] = reason
                return DeliveryResult(
                    success=False,
                    error=f"Could not auto-start target minion ({detail})",
                )

            # Wait for session to become ready (event-based, no polling)
            ready = await coordinator.wait_for_session_ready(session_id, timeout=30.0)
            if not ready:
                session_info = await coordinator.session_manager.get_session_info(session_id)
                gate_logger.warning(f"Minion {session_id} did not become ready within 30s - sending error to sender")
                return DeliveryResult(
                    success=False,
                    error=(
                        "Target minion did not start within 30 seconds "
                        f"(state: {session_info.state if session_info else 'unknown'})"
                    ),
                )
            gate_logger.info(f"Minion {session_id} is now ready")

        return DeliveryResult(success=True)

    async def send(self, comm: Comm, *, inject_timestamp: bool = True) -> DeliveryResult:
        """Send comm.content/comm.metadata to the SDK. Call after ensure_ready() succeeds."""
        success = await self._coordinator.send_message(
            session_id=comm.to_minion_id,
            message=comm.content,
            metadata=comm.metadata,
            inject_timestamp=inject_timestamp,
        )
        if not success:
            gate_logger.error(
                f"Failed to deliver comm {comm.comm_id} to minion {comm.to_minion_id} - send_message returned False"
            )
            return DeliveryResult(success=False, error="SDK rejected the message")

        return DeliveryResult(success=True)

    # =========================================================================
    # wait_for_idle=True — ported verbatim from queue_processor.py::_process_loop
    # =========================================================================

    async def _deliver_with_busy_wait(
        self,
        comm: Comm,
        *,
        reset_first: bool,
        inject_timestamp: bool,
    ) -> DeliveryResult:
        coordinator = self._coordinator
        session_id = comm.to_minion_id

        while True:
            session_info = await coordinator.session_manager.get_session_info(session_id)
            if not session_info:
                return DeliveryResult(success=False, error="Session not found")

            queue_config = getattr(session_info, 'queue_config', None) or {}

            # Issue #2159 (UC1): ACTIVE sends now (skip the wait below entirely).
            # CREATED/TERMINATED auto-start first. Every other non-ACTIVE state —
            # including a just-auto-started session and an in-flight STARTING/
            # TERMINATING found already in progress — keeps the original bounded
            # 120s wait, EXCEPT PAUSED (mid-permission-wait), which waits with NO
            # timeout: a permission decision can take as long as the user takes,
            # the same "overnight wait" philosophy _wait_for_idle() below already
            # uses. PAUSED used to fall into the same bounded wait as everything
            # else and simply fail after 120s.
            if not is_active(session_info.state):
                if session_info.state in AUTO_START_STATES:
                    gate_logger.info(f"Auto-starting session {session_id} for queue processing")
                    self._ensure_message_callback(session_id)
                    perm_callback = None
                    if coordinator._permission_callback_factory:
                        perm_callback = coordinator._permission_callback_factory(session_id)
                    success = await coordinator.start_session(session_id, perm_callback)
                    if not success:
                        gate_logger.error(f"Failed to auto-start session {session_id}")
                        return DeliveryResult(success=False, error="Failed to auto-start session")

                wait_timeout = None if is_paused(session_info.state) else 120
                if not await self._wait_for_active(session_id, timeout=wait_timeout):
                    gate_logger.error(f"Session {session_id} did not become active")
                    return DeliveryResult(success=False, error="Session did not become active")

            # Reset session if requested
            if reset_first:
                gate_logger.info(f"Resetting session {session_id} before delivery")
                self._ensure_message_callback(session_id)
                perm_callback = None
                if coordinator._permission_callback_factory:
                    perm_callback = coordinator._permission_callback_factory(session_id)
                success = await coordinator.reset_session(session_id, perm_callback, _from_queue_processor=True)
                if not success:
                    gate_logger.error(f"Failed to reset session {session_id}")
                    return DeliveryResult(success=False, error="Failed to reset session")

                # Wait for session active again after reset. Verbatim pre-PR
                # asymmetry: unlike every other failure branch in this method,
                # this one does NOT get a mark_failed-worthy error string —
                # it leaves the item pending for the next run rather than
                # permanently failing it. error=None is the signal the caller
                # (queue_processor.py) uses to tell the two apart.
                if not await self._wait_for_active(session_id):
                    gate_logger.error(f"Session {session_id} not active after reset")
                    return DeliveryResult(success=False, error=None)

            # Wait min_wait_seconds before sending
            min_wait = queue_config.get("min_wait_seconds", DEFAULT_MIN_WAIT_SECONDS)
            gate_logger.debug(f"Waiting {min_wait}s before sending to session {session_id}")
            await asyncio.sleep(min_wait)

            # Re-check pause before sending
            session_info = await coordinator.session_manager.get_session_info(session_id)
            if session_info and getattr(session_info, 'queue_paused', False):
                continue

            break

        # Send the message
        gate_logger.info(
            f"Sending queued message to session {session_id}: {comm.content[:80]}..."
        )
        send_result = await coordinator.send_message(
            session_id, comm.content, inject_timestamp=inject_timestamp
        )
        if not send_result:
            gate_logger.error(f"Failed to send message to session {session_id}")
            return DeliveryResult(success=False, error="Failed to send message")

        # Wait for SDK to finish processing before picking up next item
        min_idle = queue_config.get("min_idle_seconds", DEFAULT_MIN_IDLE_SECONDS)
        completed = await self._wait_for_idle(session_id, min_idle)
        if not completed:
            return DeliveryResult(
                success=False, error="Session entered error state during processing"
            )

        return DeliveryResult(success=True)

    # =========================================================================
    # Helpers (ported verbatim from queue_processor.py)
    # =========================================================================

    def _ensure_message_callback(self, session_id: str) -> None:
        """Ensure WebSocket message callback is registered for a session.

        When the gate auto-starts or resets a session, it bypasses the
        web_server REST endpoint that normally registers the WebSocket
        broadcast callback. This method uses the registrar injected by
        web_server to wire the callback so messages are streamed to the UI.
        """
        registrar = self._coordinator._message_callback_registrar
        if registrar:
            registrar(session_id)
            gate_logger.info(f"Registered WebSocket message callback for session {session_id}")
        else:
            gate_logger.warning(f"No message callback registrar available for session {session_id}")

    async def _wait_for_active(self, session_id: str, timeout: float | None = 120) -> bool:
        """Wait for a session to reach ACTIVE state. Returns False on error/timeout.

        timeout=None waits indefinitely — used for a session already PAUSED on a
        permission decision (issue #2159/UC1), which can take as long as the user
        takes, not a bounded SDK-startup wait.
        """
        elapsed = 0.0
        while timeout is None or elapsed < timeout:
            info = await self._coordinator.session_manager.get_session_info(session_id)
            if not info:
                return False
            if is_active(info.state):
                return True
            if is_errored(info.state):
                return False
            await asyncio.sleep(1)
            elapsed += 1
        return False

    async def _wait_for_idle(self, session_id: str, min_idle_seconds: float) -> bool:
        """
        Wait for the session to be idle for min_idle_seconds after message delivery.

        Returns True if idle reached, False if session errored.
        No timeout — supports indefinite waits (e.g., overnight permission waits).
        """
        idle_start: float | None = None

        while True:
            info = await self._coordinator.session_manager.get_session_info(session_id)
            if not info:
                return False

            # Session entered error state — caller marks the item failed.
            if is_errored(info.state):
                return False

            # Pause does NOT affect idle detection — once a message has been
            # sent to the SDK, we must complete its idle cycle before the
            # next item is picked up.  Pause only prevents the next item
            # from being picked up (checked by the caller before deliver()).

            # Treat in-flight script schedules the same as is_processing: do not
            # advance to the next queue item while a script that may be about to
            # enqueue is still running. Reaches into legion_system.scheduler_service
            # (the only back-pointer from coordinator → scheduler).
            scheduler = (
                self._coordinator.legion_system.scheduler_service
                if self._coordinator.legion_system is not None
                else None
            )
            scripts_inflight = (
                scheduler.has_inflight_scripts(session_id) if scheduler is not None else False
            )

            if info.is_processing or scripts_inflight:
                # Still busy — reset idle timer.
                idle_start = None
            else:
                # Not processing — start/continue idle timer
                if idle_start is None:
                    idle_start = asyncio.get_event_loop().time()

                elapsed_idle = asyncio.get_event_loop().time() - idle_start
                if elapsed_idle >= min_idle_seconds:
                    return True

            await asyncio.sleep(1)
