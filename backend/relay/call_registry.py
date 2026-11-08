"""RelayCallRegistry: correlate a relay tool call with its tool card (issue #2353).

The SDK does not tell an in-process MCP handler its ``tool_use_id``. A record-only
``PreToolUse`` hook (registered only for relay tools, see ``hook_matcher``) does see it, so the
hook notes ``(session, tool, input digest) -> tool_use_id`` and the handler claims the oldest
unclaimed entry for the same digest when it needs to prompt. Identical parallel calls are
interchangeable (same effect), so FIFO is sufficient. The hook never allows, asks or denies and
never raises: a hook failure must not block a tool call.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .adapters import SPECS
from .grants import input_digest

logger = logging.getLogger(__name__)

ENTRY_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class ClaimedCall:
    tool_use_id: str
    agent_id: str | None


def hook_matcher() -> str:
    """Regex matching only registered adapters' relay tools, e.g. ``mcp__relay_slack__.*``."""
    return "|".join(f"{re.escape(spec.tool_prefix)}.*" for spec in SPECS.values())


class RelayCallRegistry:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # (session_id, digest) -> deque[(expires_at, ClaimedCall)]
        self._entries: dict[tuple[str, str], deque[tuple[float, ClaimedCall]]] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for key in list(self._entries):
            queue = self._entries[key]
            while queue and queue[0][0] <= now:
                queue.popleft()
            if not queue:
                del self._entries[key]

    def record(
        self, session_id: str, tool_name: str, args: dict[str, Any], tool_use_id: str,
        agent_id: str | None = None,
    ) -> None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            self._entries.setdefault((session_id, input_digest(tool_name, args)), deque()).append(
                (now + ENTRY_TTL_SECONDS, ClaimedCall(tool_use_id, agent_id))
            )

    def claim(self, session_id: str, tool_name: str, args: dict[str, Any]) -> ClaimedCall | None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            key = (session_id, input_digest(tool_name, args))
            queue = self._entries.get(key)
            if not queue:
                return None
            _, call = queue.popleft()
            if not queue:
                del self._entries[key]
            return call

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] == session_id]:
                del self._entries[key]

    def make_hook(self, session_id: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        """The ``PreToolUse`` hook callback for one session: records and returns ``{}``."""

        async def hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
            try:
                data = input_data or {}
                call_id = data.get("tool_use_id") or tool_use_id
                tool_name = data.get("tool_name")
                tool_input = data.get("tool_input")
                if call_id and isinstance(tool_name, str) and isinstance(tool_input, dict):
                    self.record(session_id, tool_name, tool_input, call_id, data.get("agent_id"))
            except Exception:
                logger.exception("Relay call registry hook failed (ignored)")
            return {}

        return hook
