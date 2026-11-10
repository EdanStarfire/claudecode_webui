"""Inbound runtime state: cursors, dedupe ring, in-flight instructions (issue #2103 stage 3a).

``inbound_state.json``: ``{"version":1,"started_at","cursors":{channel:ts},"dedupe":[[channel,ts]],
"inflight":[entry]}``. Entries that became instructions are written (fsync) BEFORE the Slack ack,
so a crash between ack and enqueue neither loses nor repeats them; everything else is debounced.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .constants import DEDUPE_RING_SIZE, STATE_FLUSH_DEBOUNCE_SECONDS
from .persist import atomic_write_json, load_json

logger = logging.getLogger(__name__)

STATE_FILE = "inbound_state.json"


def ts_key(channel: str, ts: str) -> str:
    return f"{channel}:{ts}"


class InboundState:
    def __init__(self, relay_dir: Path, *, ring_size: int = DEDUPE_RING_SIZE) -> None:
        self._path = relay_dir / STATE_FILE
        self._ring_size = ring_size
        self.started_at: float | None = None
        self.own_app_id: str | None = None  # our Slack app id, learned from event_callback.api_app_id
        self.was_disabled = False  # the owner switched inbound off since the last enabled run
        self.cursors: dict[str, str] = {}
        self._dedupe: OrderedDict[str, None] = OrderedDict()
        self.inflight: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ load

    @property
    def existed(self) -> bool:
        return self.started_at is not None

    def load(self) -> None:
        data = load_json(self._path)
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        self.started_at = data.get("started_at")
        self.was_disabled = bool(data.get("was_disabled"))
        app_id = data.get("own_app_id")
        self.own_app_id = app_id if isinstance(app_id, str) and app_id else None
        self.cursors = {str(k): str(v) for k, v in (data.get("cursors") or {}).items()}
        for pair in data.get("dedupe") or []:
            if isinstance(pair, list) and len(pair) == 2:
                self._dedupe[ts_key(str(pair[0]), str(pair[1]))] = None
        for entry in data.get("inflight") or []:
            if isinstance(entry, dict) and entry.get("key"):
                self.inflight[entry["key"]] = entry

    def initialize_first_start(self, now: float) -> None:
        self.started_at = now

    # ---------------------------------------------------------------- dedupe

    def seen(self, channel: str, ts: str) -> bool:
        return ts_key(channel, ts) in self._dedupe

    def mark_seen(self, channel: str, ts: str) -> None:
        key = ts_key(channel, ts)
        self._dedupe[key] = None
        self._dedupe.move_to_end(key)
        while len(self._dedupe) > self._ring_size:
            self._dedupe.popitem(last=False)

    def unmark_seen(self, channel: str, ts: str) -> None:
        self._dedupe.pop(ts_key(channel, ts), None)

    @property
    def dedupe_size(self) -> int:
        return len(self._dedupe)

    # --------------------------------------------------------------- cursors

    def advance_cursor(self, channel: str, ts: str) -> None:
        current = self.cursors.get(channel)
        if current is None or _ts_float(ts) > _ts_float(current):
            self.cursors[channel] = ts

    # ----------------------------------------------------------- persistence

    def _snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "started_at": self.started_at,
            "was_disabled": self.was_disabled,
            "own_app_id": self.own_app_id,
            "cursors": dict(self.cursors),
            "dedupe": [k.split(":", 1) for k in self._dedupe],
            "inflight": list(self.inflight.values()),
        }

    async def flush(self, *, strict: bool = False) -> None:
        """Write now (fsync). A failure is logged and retried on the next flush, except with
        ``strict`` (the persist-before-ack path), where it is raised."""
        async with self._lock:
            snapshot = self._snapshot()
            try:
                await asyncio.to_thread(atomic_write_json, self._path, snapshot)
            except OSError:
                logger.exception("Could not write inbound state")
                if strict:
                    raise

    def schedule_flush(self) -> None:
        """Debounced write for state whose loss is harmless (drops, cursor moves)."""
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def later() -> None:
            await asyncio.sleep(STATE_FLUSH_DEBOUNCE_SECONDS)
            await self.flush()

        self._flush_task = loop.create_task(later())

    async def close(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            await asyncio.gather(self._flush_task, return_exceptions=True)
        await self.flush()


def _ts_float(ts: str) -> float:
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0
