"""DeliveryWatcher: follows enqueued comms to their outcome (issue #2103 stage 3b-i).

The pending store is the delivery ledger (``pending`` -> ``delivered`` | ``failed`` |
``cancelled``). The watcher polls it (in-memory work only, every 5 s) and reports terminal
outcomes to the service, which advances monitor cursors (cursor-on-confirm) or posts the single
marked reply for a failed / cancelled instruction. Watches live in memory only: after a Backend
restart nothing is watched (the pending queue is not recovered either, issue #2362), so monitor
cursors stay at their durable value and the reconnect backfill re-delivers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import constants as const

logger = logging.getLogger(__name__)

STATUS_GONE = "gone"  # the item vanished from the ledger (history cleared / session removed)


class DeliveryProbe(Protocol):
    def item_status(self, session_id: str, comm_id: str) -> str | None: ...


@dataclass
class WatchEntry:
    kind: str  # "monitor" | "instruction"
    session_id: str
    comm_id: str
    registered_at: float
    cover: dict[str, str] = field(default_factory=dict)  # monitor id -> newest ts consumed
    info: dict[str, Any] = field(default_factory=dict)  # instruction: channel / thread_ts / ...


Handler = Callable[[WatchEntry, str], Awaitable[None]]


class DeliveryWatcher:
    def __init__(
        self, probe: DeliveryProbe, on_outcome: Handler, *, now: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_seconds: float = const.WATCH_POLL_SECONDS,
        max_age: float = const.WATCH_MAX_AGE_SECONDS,
    ) -> None:
        self._probe = probe
        self._on_outcome = on_outcome
        self._now = now
        self._sleep = sleep
        self._poll = poll_seconds
        self._max_age = max_age
        self.entries: dict[str, WatchEntry] = {}
        self._task: asyncio.Task | None = None

    def watch(self, entry: WatchEntry) -> None:
        self.entries[entry.comm_id] = entry

    def drop_session(self, session_id: str) -> None:
        for comm_id in [c for c, e in self.entries.items() if e.session_id == session_id]:
            del self.entries[comm_id]

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="inbound-watcher")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.entries.clear()

    async def _run(self) -> None:
        while True:
            await self._sleep(self._poll)
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Inbound delivery watcher failed")

    async def poll_once(self) -> None:
        for comm_id, entry in list(self.entries.items()):
            status = self._probe.item_status(entry.session_id, comm_id)
            if status == "pending":
                if self._now() - entry.registered_at > self._max_age:
                    self.entries.pop(comm_id, None)  # stuck for a day: stop watching, cursor stays
                continue
            self.entries.pop(comm_id, None)
            try:
                await self._on_outcome(entry, status or STATUS_GONE)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Inbound delivery outcome handler failed")
