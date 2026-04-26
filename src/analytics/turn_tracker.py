"""TurnTracker: in-memory per-session turn counter.

A "turn" spans one user message through the next ResultMessage (or
session terminate/error). Events emitted between USER and RESULT inherit
the active turn_id; lifecycle and comm events have turn_id=None.

Turn IDs are formatted as ``"{session_id}:{counter}"`` (e.g. "abc:1").
The counter is per-session and starts at 1. Counters reset on server
restart — old turn_ids in the DB remain queryable.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TurnTracker:
    """Thread-safe (asyncio-safe) in-memory turn counter per session."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._active: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_user_message(self, session_id: str) -> str:
        """Called when a USER message is observed. Opens a new turn.

        Returns the new turn_id.
        """
        counter = self._counters.get(session_id, 0) + 1
        self._counters[session_id] = counter
        turn_id = f"{session_id}:{counter}"
        self._active[session_id] = turn_id
        logger.debug("TurnTracker: opened turn %s", turn_id)
        return turn_id

    def on_result(self, session_id: str) -> str | None:
        """Called on ResultMessage or session termination. Closes active turn.

        Returns the just-closed turn_id (or None if no turn was open).
        """
        turn_id = self._active.pop(session_id, None)
        if turn_id:
            logger.debug("TurnTracker: closed turn %s", turn_id)
        return turn_id

    def current_turn_id(self, session_id: str) -> str | None:
        """Return the currently open turn_id, or None if no turn is active."""
        return self._active.get(session_id)

    def clear_session(self, session_id: str) -> None:
        """Remove all tracking state for a session."""
        self._counters.pop(session_id, None)
        self._active.pop(session_id, None)
