"""
DeliveryGate - Consolidated message-delivery readiness gating (issue #2189,
coordinator-split PR 10; unified gate added in issue #2193 PR 3).

Originally, two independent "wait before delivering a message" gates lived
in two different files:

- comm_router.py's STARTING-only readiness wait / auto-start-via-
  is_deliverable() dance (SDK-event-driven, via wait_for_session_ready()).
- queue_processor.py's AUTO_START_STATES-gated auto-start + state-poll
  busy-wait, followed by a post-send idle busy-wait.

Those two mechanisms still live here, verbatim, as ensure_ready()/send() and
prepare_for_send()/send_queued()/wait_until_idle() — queue_processor.py keeps
calling its pair exactly as before (cuts over in PR6). Issue #2193 PR3 adds a
third mechanism alongside them: deliver(), a single unified entry point
implementing AC3 (one readiness/busy gate pair) and AC4 (full ordering
semantics over the PR2 pending-set). Issue #2193 PR5 cuts comm_router.py's
_send_to_minion() over to deliver() for real — ensure_ready()/send() remain
defined (confirmed unused by any production caller) but are not deleted here,
out of scope for a PR that changes live delivery behavior. See
test_delivery_gate.py for the ordering-matrix derivation deliver() implements.
"""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from .models.legion_models import Comm, CommType, DeliveryPolicy, SenderType
from .session_lifecycle import (
    AUTO_START_STATES,
    SessionState,
    is_active,
    is_auto_startable,
    is_deliverable,
    is_errored,
    is_paused,
)
from .timestamp_utils import get_unix_timestamp

if TYPE_CHECKING:
    from .pending_store import PendingItem
    from .session_coordinator import SessionCoordinator

gate_logger = get_logger('delivery_gate', category='DELIVERY_GATE')

# Default timing configuration (verbatim from queue_processor.py).
DEFAULT_MIN_WAIT_SECONDS = 10
DEFAULT_MIN_IDLE_SECONDS = 10

# Issue #2193 (PR 3): unified gate (deliver()) settle/timeout constants.
READINESS_SETTLE_SECONDS = 5.0
UNIFIED_READINESS_TIMEOUT_SECONDS = 120.0


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

    Three mechanisms coexist here (issue #2193 PR3):

    - ensure_ready() + send(): comm_router.py's former mechanism — no longer called
      in production as of issue #2193 PR5 (comm_router.py now calls deliver()),
      kept defined rather than deleted (out of scope for this cutover PR).
    - prepare_for_send() + send_queued() + wait_until_idle(): queue_processor.py's
      existing mechanism, untouched (cuts over in PR6).
    - deliver(): the unified AC3/AC4 mechanism below — comm_router.py's single
      production call site as of PR5. Always enqueues into
      coordinator.pending_store first (including IMMEDIATE/HALT/PIVOT items),
      then branches on comm.delivery.

    The two existing mechanisms are kept split into primitives rather than
    folded into one atomic call for reasons specific to each caller's own
    bookkeeping ordering — see the "wait_for_idle=False"/"wait_for_idle=True"
    section comments below for the detailed rationale (unchanged from
    issue #2189).
    """

    def __init__(self, coordinator: "SessionCoordinator"):
        self._coordinator = coordinator
        # Issue #2193 (PR 3): unified gate (deliver()) state.
        self._dispatch_locks: dict[str, asyncio.Lock] = {}
        self._readiness_deadlines: dict[str, float] = {}
        self._when_idle_tasks: dict[str, asyncio.Task] = {}
        # Issue #2193 (PR 4): AC5 "queued before the pivot, by whom" note.
        # session_id -> (sender_label, pivoted_at)
        self._last_pivot: dict[str, tuple[str, float]] = {}

    # =========================================================================
    # Issue #2193 (PR 3) — unified gate: deliver() and its helpers
    # =========================================================================

    async def deliver(
        self, comm: Comm, *,
        dispatch_content: str | None = None,
        dispatch_metadata: dict | None = None,
        inject_timestamp: bool | None = None,
        max_pending_size: int = 100,
    ) -> DeliveryResult:
        """Unified entry point (issue #2193 AC3/AC4). Now the single production
        call site: comm_router.py's _send_to_minion() cuts over to this in PR5
        (queue_processor.py still cuts over in PR6). Exercised directly by
        test_delivery_gate.py.

        Every comm — including IMMEDIATE/HALT/PIVOT ones — is enqueued into
        the pending store first, fixing its FIFO position at arrival time
        regardless of which gate it takes afterward. See
        test_delivery_gate.py's TestOrderingMatrix for the ordering-matrix
        derivation this guarantees.

        dispatch_content/dispatch_metadata (issue #2193 PR5): the SDK-formatted
        content/metadata to actually send, if different from comm.content/
        comm.metadata (the ledger's raw, sender-facing record) — e.g.
        comm_router.py's header/attachment-formatted message. Persisted onto
        the enqueued PendingItem rather than used directly here, because a
        WHEN_IDLE item's actual send() happens later, from
        _run_when_idle_loop's own call stack, which has no access to this
        call's local arguments.

        inject_timestamp (issue #2193 PR6): explicit override, same pattern as
        dispatch_content/dispatch_metadata — persisted onto the PendingItem and
        consulted in preference to _should_inject_timestamp()'s heuristic at
        both dispatch sites below. None (default) means "no explicit
        preference, use the heuristic." This is how session_coordinator.
        enqueue_message() avoids the heuristic entirely for the manual
        queue-message producer family (see _should_inject_timestamp()'s
        docstring for the regression this fixes).

        comm.reset_first IS consulted here as of PR6 (see
        _ensure_session_ready()) — no longer the deferred AC8 no-op it was
        through PR5.

        max_pending_size (issue #2193 PR6): forwarded to pending_store.enqueue().
        enqueue_message() reads its per-session queue_config["max_queue_size"]
        and passes it through here — queue_processor.py's enqueue_message() did
        the same against queue_manager.enqueue(); the default of 100 only
        applies to a session with no configured override.
        """
        coordinator = self._coordinator
        session_id = comm.to_minion_id

        session_dir = await coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return self._fail(comm, "Session directory not found")

        try:
            await coordinator.pending_store.enqueue(
                session_id, session_dir, comm,
                dispatch_content=dispatch_content, dispatch_metadata=dispatch_metadata,
                inject_timestamp=inject_timestamp, max_pending_size=max_pending_size,
            )
        except ValueError as e:
            return self._fail(comm, str(e))

        # Issue #2193 AC6: a paused session queues everything except an authorized
        # implicit-resume trigger (TASK/QUESTION/PIVOT from the user or an ancestor).
        # HALT/PIVOT deliveries bypass this gate entirely and fall through to their
        # own dedicated authority check below — found during review: gating them
        # here on comm_type (which a HALT/PIVOT need not set to TASK/QUESTION/PIVOT,
        # since comm_type and the HALT/PIVOT delivery policy are independent fields
        # on the public send_comm tool) would otherwise let an authorized HALT/PIVOT
        # with e.g. comm_type=REPORT silently queue instead of taking effect.
        session_info = await coordinator.session_manager.get_session_info(session_id)
        if (
            session_info
            and getattr(session_info, 'queue_paused', False)
            and comm.delivery not in (DeliveryPolicy.HALT, DeliveryPolicy.PIVOT)
        ):
            is_resume_trigger = comm.comm_type in (
                CommType.TASK, CommType.QUESTION, CommType.PIVOT,
            ) and await self._is_authorized(comm)
            if is_resume_trigger:
                await coordinator.session_manager.update_session(session_id, queue_paused=False)
                # fall through — this comm proceeds normally below, including (for
                # PIVOT) its own flush/interrupt/send.
            else:
                # Left pending: "queues instead" (AC6). A WHEN_IDLE item must still
                # start its drain loop even though it stays queued here — the loop's
                # own queue_paused check (below) makes it wait correctly rather than
                # orphaning the item until some unrelated later arrival happens to
                # reach the WHEN_IDLE branch below while unpaused (found during review).
                if comm.delivery == DeliveryPolicy.WHEN_IDLE:
                    self.ensure_when_idle_running(session_id)
                return DeliveryResult(success=True)

        if comm.delivery == DeliveryPolicy.WHEN_IDLE:
            self.ensure_when_idle_running(session_id)
            return DeliveryResult(success=True)  # fire-and-forget, same contract enqueue_message() has today

        ready = await self._ensure_session_ready(comm)
        if not ready.success:
            if ready.error is None:
                # Verbatim-preserved asymmetry (see _ensure_session_ready()'s
                # reset_first wiring): a post-reset wait timeout leaves the
                # item pending for a retry rather than marking it failed.
                return DeliveryResult(success=False, error=None)
            await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, ready.error)
            return self._fail(comm, ready.error)

        if comm.delivery == DeliveryPolicy.HALT:
            if not await self._is_authorized(comm):
                # Full, actionable wording (not a terser summary) — this is what
                # send_comm_tool surfaces back to the caller via
                # comm.metadata["delivery_failure_reason"], restoring the guidance
                # PR4's now-removed tool-layer check used to give directly.
                reason = "HALT requires the sender to be the user or an ancestor of the recipient"
                await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, reason)
                return self._fail(comm, reason)
            interrupted = await self._interrupt(session_id)
            # AC10 lists exactly 4 statuses (pending/delivered/failed/cancelled) — none
            # literally means "interrupted, never sent." "delivered" here means the gate
            # fully processed this entry and performed its intended action (the interrupt)
            # successfully. Not "cancelled" — that's reserved for AC5's distinct meaning
            # (an unrelated pending item flushed BECAUSE of someone else's pivot/halt).
            # HALT has no later step to fall back on, so (unlike PIVOT below) the ledger
            # status must reflect the interrupt's own outcome, not an assumed success.
            if interrupted:
                await coordinator.session_manager.update_session(session_id, queue_paused=True)
                await self.flush_sender(session_id, comm)
                await coordinator.pending_store.mark_delivered(session_id, session_dir, comm.comm_id)
                return DeliveryResult(success=True)
            await coordinator.pending_store.mark_failed(
                session_id, session_dir, comm.comm_id, "interrupt_session did not succeed"
            )
            return self._fail(comm, "interrupt_session did not succeed")

        if comm.delivery == DeliveryPolicy.PIVOT:
            if not await self._is_authorized(comm):
                reason = "PIVOT requires the sender to be the user or an ancestor of the recipient"
                await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, reason)
                return self._fail(comm, reason)
            await self.flush_sender(session_id, comm)
            # Swallowed deliberately (via _interrupt()) — PIVOT's ledger entry is decided
            # by the send below, which is the actually-observable completed action; an
            # interrupt that no-ops (e.g. nothing was running) must not block the send.
            await self._interrupt(session_id)
            # fall through to the same dispatch as IMMEDIATE

        lock = self._get_dispatch_lock(session_id)
        async with lock:
            # Post-lock re-check (issue #2193 PR4 race fix): a concurrent
            # flush_sender() may have cancelled this entry while it waited on the
            # readiness gate above. Whoever acquires the dispatch lock first wins.
            # success=False here leaves the ledger at "cancelled" (set by that
            # flush), not "failed" — a legitimate AC5 outcome, not an error.
            current = coordinator.pending_store.get_item(session_id, comm.comm_id)
            if current is None or current.status != "pending":
                return self._fail(comm, "Cancelled before dispatch")
            original_content = comm.content
            original_metadata = comm.metadata
            # Issue #2193 PR5: apply the dispatch override (if any) FIRST, then
            # annotate for staleness on top of that — so a stale-pivot note appends
            # onto the formatted dispatch text, not the raw, sender-facing content.
            if current.dispatch_content is not None:
                comm.content = current.dispatch_content
            if current.dispatch_metadata is not None:
                comm.metadata = current.dispatch_metadata
            comm.content = self._maybe_annotate_stale(session_id, comm, current)
            inject_ts = (
                current.inject_timestamp if current.inject_timestamp is not None
                else self._should_inject_timestamp(comm)
            )
            try:
                result = await self.send(comm, inject_timestamp=inject_ts)
            except Exception as e:
                # Without this, an exception from send_message() (vs. its documented False
                # return) would propagate out of deliver() and leave this entry stuck at
                # status="pending" forever — the item was already enqueued above, so there's
                # no path back to a terminal state without catching it here.
                gate_logger.exception(f"Unexpected error sending comm {comm.comm_id} to {session_id}")
                comm.content, comm.metadata = original_content, original_metadata
                await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, str(e))
                return self._fail(comm, str(e))
            comm.content, comm.metadata = original_content, original_metadata
            # mark_failed/mark_delivered run while still holding the dispatch lock
            # (issue #2193 PR4 race fix, strengthened during review): keeping them
            # outside the lock left a narrow window where a concurrent flush_sender()
            # could cancel this entry between the send above and the mark_* call —
            # deliver() would then still report success=True/False from the send's
            # own outcome while the ledger silently disagreed (stuck "cancelled").
            if not result.success:
                await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, result.error)
                return self._fail(comm, result.error)
            if comm.delivery == DeliveryPolicy.PIVOT:
                # Stamped only now, after a CONFIRMED successful send — not up in the
                # PIVOT branch above — so this pivot's own entry never has to special-
                # case-exclude itself from _maybe_annotate_stale()'s staleness check,
                # and a failed PIVOT send never poisons later dispatches with a note
                # about a pivot that never actually took effect (found during review).
                self._last_pivot[session_id] = (self._sender_label(comm), get_unix_timestamp())
            await coordinator.pending_store.mark_delivered(session_id, session_dir, comm.comm_id)
            return DeliveryResult(success=True)

    async def _ensure_session_ready(
        self, comm: Comm, *, timeout: float = UNIFIED_READINESS_TIMEOUT_SECONDS,
    ) -> DeliveryResult:
        """Unified auto-start + wait-for-ready + shared settle barrier (AC3).

        Standardizes on the event-based wait_for_session_ready() (comm_router.py's
        mechanism) instead of prepare_for_send()'s poll-based _wait_for_active() —
        strictly better (faster, more precise) with no downside, so the unified
        gate uses it for every target. One shared timeout (120s) regardless of
        caller.

        AC8 (issue #2193 PR5): a dormant (CREATED/TERMINATED) target only
        auto-starts if comm.auto_start is set — applied uniformly regardless of
        delivery policy (IMMEDIATE/HALT/PIVOT, and WHEN_IDLE via the loop's own
        call into this method). Before this PR, auto-start was unconditional
        here (no producer set auto_start meaningfully); send_comm's migration
        (this same PR) is what gives it a real, non-default-False value.
        """
        coordinator = self._coordinator
        session_id = comm.to_minion_id

        session_info = await coordinator.session_manager.get_session_info(session_id)
        if not session_info:
            return DeliveryResult(success=False, error="Target minion not found")

        if is_errored(session_info.state):
            # Unified behavior change (flagged, confirmed): comm_router's current
            # ensure_ready() would auto-restart an ERRORED target (ERROR isn't in
            # is_deliverable(), so it falls into the auto-start branch) — an
            # apparent accident of is_deliverable()'s boolean shape, not a
            # designed behavior. prepare_for_send() already fails fast on ERROR.
            # The unified gate standardizes on the fail-fast behavior, requiring
            # explicit user recovery.
            return DeliveryResult(success=False, error="Target minion is in ERROR state")

        waited = False
        if is_auto_startable(session_info.state):  # CREATED, TERMINATED
            if not comm.auto_start:
                return DeliveryResult(
                    success=False,
                    error="Target minion is stopped and auto_start was not requested",
                )
            self._ensure_message_callback(session_id)
            permission_callback = None
            if coordinator._permission_callback_factory:
                permission_callback = coordinator._permission_callback_factory(session_id)
            started = await coordinator.start_session(session_id, permission_callback)
            if not started:
                return DeliveryResult(success=False, error="Could not auto-start target minion")
            waited = True
        elif not is_deliverable(session_info.state):  # STARTING, TERMINATING
            waited = True
        # else: ACTIVE or PAUSED already (is_deliverable) — nothing to wait for,
        # matching ensure_ready()'s existing, test-validated treatment of PAUSED
        # as already-fine (TestPausedTargetDeliversWithoutForceStart), not
        # prepare_for_send()'s treatment (which only accepts is_active() and
        # would send a PAUSED session through an unbounded wait). Unifying on
        # ensure_ready()'s reading here specifically.

        if waited:
            ready = await coordinator.wait_for_session_ready(session_id, timeout=timeout)
            if not ready:
                return DeliveryResult(success=False, error="Target minion did not become ready in time")
            self._readiness_deadlines[session_id] = (
                asyncio.get_event_loop().time() + READINESS_SETTLE_SECONDS
            )

        if comm.reset_first:
            self._ensure_message_callback(session_id)
            permission_callback = None
            if coordinator._permission_callback_factory:
                permission_callback = coordinator._permission_callback_factory(session_id)
            reset_ok = await coordinator.reset_session(
                session_id, permission_callback, _from_queue_processor=True
            )
            if not reset_ok:
                return DeliveryResult(success=False, error="Failed to reset session")
            reset_ready = await coordinator.wait_for_session_ready(session_id, timeout=timeout)
            if not reset_ready:
                # Verbatim-preserved asymmetry from prepare_for_send() — see
                # DeliveryResult's own docstring. A transient post-reset timing
                # hiccup, not a real failure: leave the item pending for a
                # retry rather than permanently failing it.
                return DeliveryResult(success=False, error=None)
            self._readiness_deadlines[session_id] = (
                asyncio.get_event_loop().time() + READINESS_SETTLE_SECONDS
            )

        # The settle deadline is anchored to a start/reset event, not re-armed by
        # every arrival (AC3: "after a session start or reset"). A comm reaching
        # this method while the session was already ready skips straight through
        # (no `waited`, no new deadline) and only respects a still-open window
        # left by an earlier caller's start.
        deadline = self._readiness_deadlines.get(session_id)
        if deadline is not None:
            now = asyncio.get_event_loop().time()
            if now < deadline:
                await asyncio.sleep(deadline - now)

        return DeliveryResult(success=True)

    def _fail(self, comm: Comm, error: str) -> DeliveryResult:
        """Every deliver() failure routes through here (issue #2193 PR5): sets
        comm.metadata["delivery_failure_reason"], the key send_comm_tool already
        reads back after route_comm() returns False. Widens the ensure_ready()-era
        auto-start-failure-only behavior to every failure path."""
        comm.metadata["delivery_failure_reason"] = error
        return DeliveryResult(success=False, error=error)

    async def _wait_for_busy_clear(self, session_id: str) -> bool:
        """Busy gate for a when_idle item with no settle (AC3) — the moment
        is_processing clears, return True. Distinct from _wait_for_idle()'s
        continuous-idle-window semantics, which stays reserved for
        SenderType.SCHEDULE's post-send settle option (AC3)."""
        while True:
            info = await self._coordinator.session_manager.get_session_info(session_id)
            if not info:
                return False
            if is_errored(info.state):
                return False
            if not info.is_processing:
                return True
            await asyncio.sleep(1)

    def _get_dispatch_lock(self, session_id: str) -> asyncio.Lock:
        """Serializes actual send() calls per session so concurrent immediate
        dispatches (and the background when_idle loop) never interleave."""
        lock = self._dispatch_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._dispatch_locks[session_id] = lock
        return lock

    async def _interrupt(self, session_id: str) -> bool:
        """Consolidates the try/except+log pattern comm_router.py's HALT/PIVOT
        branches each duplicate today — for this gate's own internal reuse
        only; comm_router.py itself is untouched in this PR. Returns whether
        the interrupt actually succeeded, for HALT's own ledger-accuracy use
        (see deliver()) — PIVOT's caller ignores the return value, since a
        no-op interrupt there must not block the send that follows."""
        try:
            interrupted = await self._coordinator.interrupt_session(session_id)
            if not interrupted:
                gate_logger.warning(f"interrupt_session returned False for session {session_id}")
            return interrupted
        except Exception as e:
            gate_logger.warning(f"Failed to interrupt session {session_id}: {e}")
            return False

    async def flush_sender(self, session_id: str, trigger_comm: Comm) -> list["PendingItem"]:
        """Cancel every still-pending item from the same sender as trigger_comm, to this
        recipient, excluding trigger_comm's own just-enqueued entry (issue #2193 AC5).
        Acquires the same per-session dispatch lock deliver()/_run_when_idle_loop use for
        their send() calls, so a flush can never race a dispatch already in flight —
        whoever gets the lock first decides the outcome (race fix above)."""
        coordinator = self._coordinator
        session_dir = await coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return []

        cancelled: list[PendingItem] = []
        lock = self._get_dispatch_lock(session_id)
        async with lock:
            for item in coordinator.pending_store.get_pending(session_id):
                if item.comm.comm_id == trigger_comm.comm_id:
                    continue
                if not self._same_sender(item.comm, trigger_comm):
                    continue
                cancelled_item = await coordinator.pending_store.cancel(
                    session_id, session_dir, item.comm.comm_id
                )
                if cancelled_item:
                    cancelled.append(cancelled_item)
        return cancelled

    @staticmethod
    def _same_sender(a: Comm, b: Comm) -> bool:
        if a.from_user or b.from_user:
            return a.from_user and b.from_user
        if a.from_minion_id or b.from_minion_id:
            return a.from_minion_id == b.from_minion_id
        return a.sender_type == b.sender_type and a.sender_id == b.sender_id

    @staticmethod
    def _sender_label(comm: Comm) -> str:
        if comm.from_user:
            return "the user"
        return comm.from_minion_name or comm.from_minion_id or "someone"

    def _maybe_annotate_stale(self, session_id: str, comm: Comm, item: "PendingItem") -> str:
        """Appends the AC5 staleness note to the content actually sent, without mutating
        comm.content permanently — matches comm_router.py's existing swap-and-restore
        convention for PIVOT/NORMAL dispatch (ported, not invented here).

        `_last_pivot` is only stamped after a pivot's OWN dispatch has already
        succeeded (see deliver()'s PIVOT branch) — so a pivot's own entry is never
        compared against its own stamp here, and a failed pivot never stamps at all.
        """
        last_pivot = self._last_pivot.get(session_id)
        if last_pivot and last_pivot[1] > item.created_at:
            note = f"\n\n---\n_Note: this message was queued before a pivot from {last_pivot[0]}._"
            return comm.content + note
        return comm.content

    async def _is_authorized(self, comm: Comm) -> bool:
        """True if comm's sender may HALT/PIVOT its recipient, or implicitly resume a
        paused session (issue #2193 AC6/AC7). The user is always authorized, checked
        before the legion_system=None guard below — found during review: the naive
        ordering silently denied a user-originated action whenever legion_system
        happened to be unset, contradicting is_authorized_sender()'s own "the user
        is always authorized" rule. Otherwise guards the non-Legion case the same
        way _wait_for_idle() already guards legion_system being None for scheduler
        access."""
        if comm.from_user:
            return True
        legion_system = self._coordinator.legion_system
        if legion_system is None:
            return False
        return await legion_system.comm_router.is_authorized_sender(comm)

    @staticmethod
    def _should_inject_timestamp(comm: Comm) -> bool:
        """Issue #1779's rule ("user messages get timestamp injection, inter-agent/
        system messages don't") is scoped by SUBSYSTEM, not by who authored the
        comm (session_coordinator.send_message()'s docstring: True for the live
        user-send/queue/cron paths; "Legion inter-agent comms... (comm_router.py)
        pass False" — unconditionally, regardless of comm.from_user). Every comm
        reaching deliver() today flows through comm_router.py, so SenderType.USER
        must NOT return True here — a user-sent Legion comm (e.g. via
        POST /api/legions/{id}/comms) is still Legion-routed traffic, persisted to
        timeline.jsonl pre-injection; injecting into the SDK-delivered copy would
        desync the two, exactly what the docstring warns against. Found as a live
        bug in review (issue #2193 PR5): dormant since PR3, this cutover is what
        makes it reachable for the first time.

        SCHEDULE stays True: today's schedule-fired prompts are constructed with
        from_user=True by queue_processor.py's Comm wrapper and already get
        injection by default via the queue/cron path (a different subsystem,
        under the docstring's "live user-send and queue/cron paths" rule) — this
        preserves that observed behavior under the new discriminator, for PR6 to
        confirm/keep when it migrates the scheduler onto deliver() for real (PR6
        will also need to make queue_processor's Comm wrapper set sender_type=
        SCHEDULE explicitly, since today it's inferred as USER from from_user=True
        and isn't yet exercised by this method at all)."""
        return comm.sender_type == SenderType.SCHEDULE

    def ensure_when_idle_running(self, session_id: str) -> None:
        """Starts the per-session background when_idle drain loop if not
        already running — idempotent, mirrors QueueProcessor.ensure_running()."""
        task = self._when_idle_tasks.get(session_id)
        if task and not task.done():
            return
        self._when_idle_tasks[session_id] = asyncio.create_task(
            self._run_when_idle_loop(session_id), name=f"delivery-gate-when-idle-{session_id}",
        )

    def stop_when_idle(self, session_id: str) -> None:
        task = self._when_idle_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    def is_when_idle_running(self, session_id: str) -> bool:
        task = self._when_idle_tasks.get(session_id)
        return task is not None and not task.done()

    async def _run_when_idle_loop(self, session_id: str) -> None:
        """Per-session background drain of WHEN_IDLE pending items (AC3/AC4).
        Generalizes queue_processor._process_loop()'s busy-wait mechanics onto
        pending_store — but queue_processor.py itself is not rewired onto this
        yet (still PR6); comm_router.py's send_comm-originated WHEN_IDLE items
        (any send_comm call with delivery="when_idle" — AC2's per-comm_type
        default, e.g. REPORT, is defined in legion_models.default_delivery_for_type()
        but not yet wired into send_comm_tool's own default) are this loop's
        first real traffic, as of issue #2193 PR5."""
        coordinator = self._coordinator
        # Identifies this task so the finally block below doesn't evict a *newer*
        # task that ensure_when_idle_running() may have already started for this
        # session_id by the time this one is cancelled and unwinding (stop_when_idle()
        # pops+cancels, but cancellation delivery isn't instantaneous).
        current_task = asyncio.current_task()
        gate_logger.info(f"when_idle drain loop started for session {session_id}")
        try:
            while True:
                session_dir = await coordinator.session_manager.get_session_directory(session_id)
                if not session_dir:
                    break
                session_info = await coordinator.session_manager.get_session_info(session_id)
                if session_info and getattr(session_info, 'queue_paused', False):
                    await asyncio.sleep(1)
                    continue
                when_idle_pending = [
                    item for item in coordinator.pending_store.get_pending(session_id)
                    if item.comm.delivery == DeliveryPolicy.WHEN_IDLE
                ]
                if not when_idle_pending:
                    break
                item = when_idle_pending[0]  # lowest position = earliest arrival
                comm = item.comm

                ready = await self._ensure_session_ready(comm)
                if not ready.success:
                    if ready.error is None:
                        # Leave pending; don't hot-loop — matches
                        # prepare_for_send()'s break-either-way on a post-reset
                        # wait timeout. Using `continue` here would re-attempt
                        # this same reset-pending item immediately, with no
                        # delay, forever.
                        break
                    await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, ready.error)
                    self._fail(comm, ready.error)
                    continue

                is_schedule = comm.sender_type == SenderType.SCHEDULE
                if is_schedule:
                    queue_config = await self._queue_config_for(session_id)
                    await asyncio.sleep(queue_config.get("min_wait_seconds", DEFAULT_MIN_WAIT_SECONDS))

                if not await self._wait_for_busy_clear(session_id):
                    error = "Session entered error state"
                    await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, error)
                    self._fail(comm, error)
                    continue

                lock = self._get_dispatch_lock(session_id)
                async with lock:
                    # Post-lock re-check (issue #2193 PR4 race fix): a concurrent
                    # flush_sender() may have cancelled this entry while it sat
                    # mid-wait above (busy-clear/schedule settle). Whoever acquires
                    # the dispatch lock first wins.
                    current = coordinator.pending_store.get_item(session_id, comm.comm_id)
                    if current is None or current.status != "pending":
                        continue
                    original_content = comm.content
                    original_metadata = comm.metadata
                    # Issue #2193 PR5: same dispatch_content/dispatch_metadata override +
                    # stale-annotate composition as deliver()'s inline dispatch above —
                    # read off this same already-fetched `current` PendingItem, since this
                    # is the call stack where a WHEN_IDLE item's send actually happens.
                    if current.dispatch_content is not None:
                        comm.content = current.dispatch_content
                    if current.dispatch_metadata is not None:
                        comm.metadata = current.dispatch_metadata
                    comm.content = self._maybe_annotate_stale(session_id, comm, current)
                    inject_ts = (
                        current.inject_timestamp if current.inject_timestamp is not None
                        else self._should_inject_timestamp(comm)
                    )
                    try:
                        result = await self.send(comm, inject_timestamp=inject_ts)
                    except Exception as e:
                        # Same reasoning as deliver()'s inline dispatch: an exception here
                        # (vs. send()'s documented False return) would otherwise leave this
                        # entry stuck at status="pending" and the loop stuck retrying it
                        # forever (it's still the head of when_idle_pending next iteration).
                        gate_logger.exception(f"Unexpected error sending comm {comm.comm_id} to {session_id}")
                        comm.content, comm.metadata = original_content, original_metadata
                        await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, str(e))
                        self._fail(comm, str(e))
                        continue
                    comm.content, comm.metadata = original_content, original_metadata
                    # mark_failed/mark_delivered run while still holding the dispatch lock
                    # (issue #2193 PR5 — matches the inline dispatch path above, closing a
                    # PR4 inconsistency: running these after releasing the lock left a
                    # narrow window where a concurrent flush_sender() could cancel this
                    # entry between the send above and the mark_* call, potentially
                    # leaving a genuinely-delivered item mislabeled "cancelled").
                    if not result.success:
                        await coordinator.pending_store.mark_failed(session_id, session_dir, comm.comm_id, result.error)
                        self._fail(comm, result.error)
                        continue
                    await coordinator.pending_store.mark_delivered(session_id, session_dir, comm.comm_id)

                if is_schedule:
                    queue_config = await self._queue_config_for(session_id)
                    min_idle = queue_config.get("min_idle_seconds", DEFAULT_MIN_IDLE_SECONDS)
                    if not await self._wait_for_idle(session_id, min_idle):
                        # _wait_for_idle() returning False means the session errored
                        # mid-settle, after a successful send -- nothing left to mark
                        # failed (the item already delivered); just stop this
                        # session's loop, mirroring queue_processor._process_loop()'s
                        # same break.
                        break
        except asyncio.CancelledError:
            gate_logger.info(f"when_idle drain loop cancelled for session {session_id}")
        finally:
            if self._when_idle_tasks.get(session_id) is current_task:
                self._when_idle_tasks.pop(session_id, None)
            gate_logger.info(f"when_idle drain loop exited for session {session_id}")

    async def _queue_config_for(self, session_id: str) -> dict:
        info = await self._coordinator.session_manager.get_session_info(session_id)
        return (getattr(info, 'queue_config', None) or {}) if info else {}

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
    #
    # Split into prepare_for_send() / send_queued() / wait_until_idle() rather
    # than one atomic step, for the same reason the wait_for_idle=False branch
    # is split into ensure_ready()/send(): pre-PR, queue_processor.py called
    # mark_sent()/broadcast("sent") immediately after send_message() succeeded
    # ("Mark as sent immediately — the message has been delivered to the SDK"),
    # BEFORE the post-send min_idle_seconds busy-wait. Bundling send+idle-wait
    # into one atomic deliver() call would force that bookkeeping to wait for
    # the full idle-wait too — a real, user-visible delay to the UI's "sent"
    # status (by min_idle_seconds, default 10s) on every queued message, not
    # just an internal-mechanics detail.
    # =========================================================================

    async def prepare_for_send(
        self,
        comm: Comm,
        *,
        reset_first: bool = False,
    ) -> DeliveryResult:
        """AUTO_START_STATES-gated auto-start + optional reset + the
        min_wait_seconds delay + pause re-check — everything up to but not
        including the actual send. Call send_queued() after this succeeds.
        """
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

        return DeliveryResult(success=True)

    async def send_queued(self, comm: Comm, *, inject_timestamp: bool = True) -> DeliveryResult:
        """Send comm.content to the SDK — queue_processor.py's calling
        convention (positional session_id/content, no metadata param), kept
        distinct from send()'s keyword/metadata convention per-branch, not
        unified. Call after prepare_for_send() succeeds.
        """
        session_id = comm.to_minion_id
        gate_logger.info(
            f"Sending queued message to session {session_id}: {comm.content[:80]}..."
        )
        send_result = await self._coordinator.send_message(
            session_id, comm.content, inject_timestamp=inject_timestamp
        )
        if not send_result:
            gate_logger.error(f"Failed to send message to session {session_id}")
            return DeliveryResult(success=False, error="Failed to send message")

        return DeliveryResult(success=True)

    async def wait_until_idle(self, comm: Comm) -> DeliveryResult:
        """Post-send min_idle_seconds busy-wait. Call after the caller has
        already done its own mark_sent/broadcast("sent") bookkeeping — this
        step must not gate that (see class docstring).
        """
        session_id = comm.to_minion_id
        session_info = await self._coordinator.session_manager.get_session_info(session_id)
        queue_config = getattr(session_info, 'queue_config', None) or {} if session_info else {}
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
