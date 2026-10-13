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

    return current_state


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


def is_interruptible(state: SessionState) -> bool:
    """True if ``interrupt_session()`` may act on a session in this state.

    PAUSED is interruptible (UC4/#2159): interrupting a session paused on a permission
    decision denies that session's own pending permission(s) and returns it to ACTIVE.
    """
    return state in (SessionState.ACTIVE, SessionState.PAUSED)
