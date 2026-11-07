"""One-shot approval grants for relay tool calls (issue #2103).

The permission callback records a grant when the user approves a prompt; the tool
handler consumes it. A grant is bound to (session, tool, canonical input hash), expires
after 60 s and is single use, so an approval cannot be replayed for different text or a
different channel. Under bypassPermissions / pre-approved rules the callback never runs,
no grant exists, and the handler fails closed.
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
        self._grants: dict[tuple[str, str], tuple[float, Grant]] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for key in [k for k, (exp, _) in self._grants.items() if exp <= now]:
            del self._grants[key]

    def add(self, session_id: str, tool: str, args: dict[str, Any], approval: str) -> None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            self._grants[(session_id, input_digest(tool, args))] = (
                now + GRANT_TTL_SECONDS,
                Grant(approval),
            )

    def consume(self, session_id: str, tool: str, args: dict[str, Any]) -> Grant | None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            entry = self._grants.pop((session_id, input_digest(tool, args)), None)
        return entry[1] if entry else None

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._grants if k[0] == session_id]:
                del self._grants[key]
