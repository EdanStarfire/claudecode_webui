"""Single source of truth for session lifecycle state (epic #1990 stage 6a).

One ``SessionState`` enum, one ``LifecycleEvent``-driven ``transition()`` entry point, and
the shared predicates every caller (routers, queue processor, scheduler, comm router,
Legion MCP tools, fleet ops) consults instead of hand-rolling its own state-tuple check.

``transition()`` is a pure function: given a current state, an event, and the handful of
externally-observed facts a transition may depend on (open permission count, whether the
caller is already terminated, etc.), it returns the resulting state. It never touches
disk, never emits events, and never raises for an invalid (state, event) pair — an event
with no effect from a given state simply returns that state unchanged. Callers (chiefly
``SessionManager``) are responsible for persisting the result and emitting exactly one
``state_change`` event when the returned state differs from the one they started with.
"""

from __future__ import annotations

from enum import Enum


class SessionState(Enum):
    """Session lifecycle states."""
    CREATED = "created"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    ERROR = "error"


# Named SessionState groupings — update these when adding new states.
STOPPED_STATES: frozenset[SessionState] = frozenset({
    SessionState.CREATED, SessionState.PAUSED,
    SessionState.TERMINATED, SessionState.ERROR,
})
STARTABLE_STATES: frozenset[SessionState] = frozenset({
    SessionState.CREATED, SessionState.PAUSED, SessionState.TERMINATED,
})
RUNNABLE_STATES: frozenset[SessionState] = frozenset({
    SessionState.ACTIVE, SessionState.STARTING,
})
AUTO_START_STATES: frozenset[SessionState] = frozenset({
    SessionState.CREATED, SessionState.TERMINATED,
})


class LifecycleEvent(Enum):
    """Events that drive a SessionState transition through ``transition()``."""
    START_REQUESTED = "start_requested"          # STARTABLE_STATES -> STARTING
    SDK_READY = "sdk_ready"                      # STARTING -> ACTIVE
    PERMISSION_OPENED = "permission_opened"      # ACTIVE -> PAUSED (computed)
    PERMISSION_CLOSED = "permission_closed"      # PAUSED -> ACTIVE (computed)
    TERMINATE_REQUESTED = "terminate_requested"  # not-TERMINATED -> TERMINATING
    TERMINATED = "terminated"                    # TERMINATING -> TERMINATED
    ERROR_OCCURRED = "error_occurred"            # any -> ERROR
    TURN_STARTED = "turn_started"                # processing: False -> True
    TURN_ENDED = "turn_ended"                    # processing: True -> False
    RECOVERY_REQUESTED = "recovery_requested"    # any -> TERMINATED (unguarded)
    BOOT_NORMALIZED = "boot_normalized"          # RUNNABLE_STATES -> CREATED


def transition(
    current_state: SessionState,
    event: LifecycleEvent,
    *,
    open_permission_count: int = 0,
) -> SessionState:
    """Compute the resulting state for ``event`` given ``current_state``.

    PAUSED/ACTIVE around permission requests is computed from
    ``open_permission_count`` (issue #1746's per-session open-request tracking,
    surfaced via ``PermissionService.open_permission_count()``) rather than supplied
    directly by the caller — a caller only ever reports that a permission opened or
    closed, never a target state. An event with no effect from ``current_state``
    returns ``current_state`` unchanged (a no-op, not an error).
    """
    if event is LifecycleEvent.START_REQUESTED:
        if current_state in STARTABLE_STATES:
            return SessionState.STARTING
        return current_state

    if event is LifecycleEvent.SDK_READY:
        if current_state is SessionState.STARTING:
            return SessionState.ACTIVE
        return current_state

    if event is LifecycleEvent.PERMISSION_OPENED:
        if current_state is SessionState.ACTIVE and open_permission_count > 0:
            return SessionState.PAUSED
        return current_state

    if event is LifecycleEvent.PERMISSION_CLOSED:
        if current_state is SessionState.PAUSED and open_permission_count == 0:
            return SessionState.ACTIVE
        return current_state

    if event is LifecycleEvent.TERMINATE_REQUESTED:
        if current_state is SessionState.TERMINATED:
            return current_state
        return SessionState.TERMINATING

    if event is LifecycleEvent.TERMINATED:
        if current_state is SessionState.TERMINATING:
            return SessionState.TERMINATED
        return current_state

    if event is LifecycleEvent.ERROR_OCCURRED:
        # Unguarded by design, matching pre-stage-6 behavior: a critical SDK error can
        # land from any state, including mid-start or mid-terminate.
        return SessionState.ERROR

    if event is LifecycleEvent.RECOVERY_REQUESTED:
        # Unguarded like ERROR_OCCURRED: force any state back to TERMINATED in a single
        # hop (no TERMINATING flash) so a restart/reset/auto-resume can immediately
        # re-start from a STARTABLE state without losing claude_code_session_id.
        return SessionState.TERMINATED

    if event is LifecycleEvent.BOOT_NORMALIZED:
        if current_state in RUNNABLE_STATES:
            return SessionState.CREATED
        return current_state

    return current_state


def transition_processing(current_processing: bool, event: LifecycleEvent) -> bool:
    """Compute the resulting ``is_processing`` value for ``event``.

    Parallel to ``transition()`` rather than folded into its return type, so adding
    processing-state events doesn't change ``transition()``'s signature for every
    existing caller. An event with no effect on processing state returns
    ``current_processing`` unchanged.
    """
    if event is LifecycleEvent.TURN_STARTED:
        return True

    if event in (
        LifecycleEvent.TURN_ENDED,
        LifecycleEvent.ERROR_OCCURRED,
        LifecycleEvent.RECOVERY_REQUESTED,
        LifecycleEvent.TERMINATED,
        # Issue #2184: boot always resets is_processing to False — no SDK is running
        # yet to be mid-turn, regardless of what the persisted snapshot says.
        LifecycleEvent.BOOT_NORMALIZED,
    ):
        return False

    return current_processing


def is_startable(state: SessionState) -> bool:
    """True if ``start_session()`` may be called from this state."""
    return state in STARTABLE_STATES


def is_auto_startable(state: SessionState) -> bool:
    """True if a queued/scheduled delivery may auto-start a session from this state."""
    return state in AUTO_START_STATES


def is_deliverable(state: SessionState) -> bool:
    """True if a message/comm can be delivered to a session in this state right now.

    ACTIVE delivers immediately; PAUSED queues/waits and delivers once the session
    returns to ACTIVE (issue UC1/#2159) — it is never an error or a stall.
    """
    return state in (SessionState.ACTIVE, SessionState.PAUSED)


RESTARTABLE_STATES = frozenset({SessionState.ACTIVE, SessionState.ERROR, SessionState.TERMINATED})


def is_restartable(state: SessionState) -> bool:
    """True if "Restart Agent" (recover -> start) may act on a session in this state.

    Issue #2293: ACTIVE (unstick a live turn), ERROR and TERMINATED (retry/revive with
    the same ID and history). Deliberately NOT deliverability: PAUSED is blocked on a
    permission decision (#2159), STARTING/TERMINATING have a transition in flight that
    recover_session() would clobber, and CREATED was never started — Start is the right
    action there, not recovery.
    """
    return state in RESTARTABLE_STATES


def is_interruptible(state: SessionState) -> bool:
    """True if ``interrupt_session()`` may act on a session in this state.

    PAUSED is interruptible (UC4/#2159): interrupting a session paused on a permission
    decision denies that session's own pending permission(s) and returns it to ACTIVE.
    """
    return state in (SessionState.ACTIVE, SessionState.PAUSED)


def is_live(state: SessionState) -> bool:
    """True if the session has an actively-running SDK instance (ACTIVE or STARTING)."""
    return state in RUNNABLE_STATES


def is_not_terminated(state: SessionState) -> bool:
    """True if the session has not reached the terminal TERMINATED state."""
    return state is not SessionState.TERMINATED


def is_active(state: SessionState) -> bool:
    """True if the session is in the single live, immediately-deliverable ACTIVE state."""
    return state is SessionState.ACTIVE


def is_paused(state: SessionState) -> bool:
    """True if the session is paused on an open permission decision."""
    return state is SessionState.PAUSED


def is_errored(state: SessionState) -> bool:
    """True if the session has hit an unrecoverable runtime error."""
    return state is SessionState.ERROR


def is_terminal(state: SessionState) -> bool:
    """True if the session is no longer running and not auto-recoverable without an
    explicit action (TERMINATED or ERROR).

    Distinct from ``is_not_terminated()``, which only excludes TERMINATED — an ERROR
    session is "not terminated" by that predicate's definition but is still terminal
    in the sense this predicate captures.
    """
    return state in (SessionState.TERMINATED, SessionState.ERROR)
