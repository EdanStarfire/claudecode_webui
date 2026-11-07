"""Backend-wide ``relay_handle`` <-> session_id index (issue #2103).

Built from SessionManager at startup and kept current on set/delete. Handles
are assigned once (``slugify_name(name)`` with numeric suffix on collision) and
are never re-derived on rename.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable

from backend.slug_utils import slugify_name

from .safety import HANDLE_RE, RESERVED_HANDLES, validate_handle


class HandleConflictError(ValueError):
    """The requested handle is already used by another session."""


class RelayHandleIndex:
    def __init__(self, is_live: Callable[[str], bool] | None = None) -> None:
        # is_live(session_id): lets the index drop handles of sessions deleted by any path.
        self._is_live = is_live
        self._by_handle: dict[str, str] = {}
        self._by_session: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def build(self, sessions: Iterable[tuple[str, str | None]]) -> None:
        """Rebuild from (session_id, relay_handle) pairs (startup)."""
        self._by_handle.clear()
        self._by_session.clear()
        for session_id, handle in sessions:
            if handle and handle not in self._by_handle:
                self._by_handle[handle] = session_id
                self._by_session[session_id] = handle

    def _owner(self, handle: str) -> str | None:
        owner = self._by_handle.get(handle)
        if owner is not None and self._is_live is not None and not self._is_live(owner):
            self.remove_session(owner)
            return None
        return owner

    def get_session(self, handle: str) -> str | None:
        return self._owner(handle)

    def get_handle(self, session_id: str) -> str | None:
        return self._by_session.get(session_id)

    def is_taken(self, handle: str, *, excluding_session: str | None = None) -> bool:
        owner = self._owner(handle)
        return owner is not None and owner != excluding_session

    def remove_session(self, session_id: str) -> None:
        handle = self._by_session.pop(session_id, None)
        if handle and self._by_handle.get(handle) == session_id:
            del self._by_handle[handle]

    def _bind(self, session_id: str, handle: str) -> None:
        old = self._by_session.get(session_id)
        if old and self._by_handle.get(old) == session_id:
            del self._by_handle[old]
        self._by_handle[handle] = session_id
        self._by_session[session_id] = handle

    def rebind(self, session_id: str, handle: str) -> None:
        self._bind(session_id, handle)

    async def set_handle(self, session_id: str, handle: str) -> str:
        """Validate and reserve *handle* for the session (check+set under one lock)."""
        validate_handle(handle)
        async with self._lock:
            if self.is_taken(handle, excluding_session=session_id):
                raise HandleConflictError(f"relay_handle '{handle}' is already in use")
            self._bind(session_id, handle)
        return handle

    async def assign_default(self, session_id: str, session_name: str | None) -> str:
        """Reserve a derived handle for a session that has none; returns it."""
        async with self._lock:
            existing = self._by_session.get(session_id)
            if existing:
                return existing
            base = default_handle_base(session_name, session_id)
            candidate, n = base, 1
            while self.is_taken(candidate) or candidate in RESERVED_HANDLES:
                n += 1
                suffix = f"-{n}"
                candidate = f"{base[: 32 - len(suffix)].rstrip('-')}{suffix}"
            self._bind(session_id, candidate)
            return candidate


def default_handle_base(session_name: str | None, session_id: str | None = None) -> str:
    """``slugify_name(name)`` clipped to 32 chars; falls back to ``session-{uuid[:6]}``."""
    base = slugify_name(session_name or "")[:32].strip("-")
    if not HANDLE_RE.match(base) or base in RESERVED_HANDLES:
        base = f"session-{(session_id or uuid.uuid4().hex)[:6]}".lower()
        base = "".join(c for c in base if c.isalnum() or c == "-")
    return base
