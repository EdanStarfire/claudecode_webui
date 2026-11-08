"""One-shot approval grants for relay tool calls (issue #2103).

The permission callback records a grant when the user approves a prompt; the tool
handler consumes it. A grant is bound to (session, tool, canonical input hash), expires
after 60 s and is single use, so an approval cannot be replayed for different text or a
different channel. Only the permission callback's prompt creates a grant (default /
acceptEdits). Where the callback never runs (auto, bypassPermissions, pre-approved rules) no
grant exists and the handler prompts the owner itself (issue #2353); that answer is consumed
directly and mints no grant.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

GRANT_TTL_SECONDS = 60.0


def input_digest(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{tool}\0{canonical}".encode()).hexdigest()


@dataclass(frozen=True)
class Grant:
    approval: str  # prompt_allow | prompt_promote


class GrantStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._grants: dict[tuple[str, str], list[tuple[float, Grant]]] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for key in list(self._grants):
            live = [e for e in self._grants[key] if e[0] > now]
            if live:
                self._grants[key] = live
            else:
                del self._grants[key]

    def add(self, session_id: str, tool: str, args: dict[str, Any], approval: str) -> None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            # one entry per approved prompt: two identical parallel sends are approved separately
            self._grants.setdefault((session_id, input_digest(tool, args)), []).append(
                (now + GRANT_TTL_SECONDS, Grant(approval))
            )

    def consume(self, session_id: str, tool: str, args: dict[str, Any]) -> Grant | None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            key = (session_id, input_digest(tool, args))
            entries = self._grants.get(key)
            if not entries:
                return None
            _, grant = entries.pop(0)
            if not entries:
                del self._grants[key]
        return grant

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._grants if k[0] == session_id]:
                del self._grants[key]
