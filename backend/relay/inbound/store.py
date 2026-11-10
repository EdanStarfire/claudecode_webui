"""JsonSubscriptionStore: durable subscription / thread / monitor records (issue #2103 stage 3a/3b).

``data/relays/{relay_id}/subscriptions.json`` = ``{"version":1,"subscriptions":[...]}``. 3a writes
``kind="instruction_thread"`` records; 3b-i adds ``monitor_thread`` / ``monitor_channel`` on the same
storage (unknown fields are tolerated on load). The in-memory index is the read path (thread-grant
and monitor lookups are synchronous); structural mutations write through, cursor / counter updates
are debounced (a lost update only costs a re-fetch, see the monitor cursor design).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from . import constants as const
from .constants import (
    MAX_THREAD_RECORDS,
    MONITOR_KINDS,
    STATE_FLUSH_DEBOUNCE_SECONDS,
    THREAD_KIND,
    THREAD_TTL_SECONDS,
)
from .persist import atomic_write_json, load_json

logger = logging.getLogger(__name__)

STORE_FILE = "subscriptions.json"
ACTIVE = "active"
SUSPENDED = "suspended"


class MonitorLimitError(Exception):
    """A monitor cap (per session / per relay) is reached; ``code`` is ``monitor_limit``."""

    code = "monitor_limit"


class JsonSubscriptionStore:
    def __init__(self, relay_dir: Path, relay_id: str, *, clock=time.time) -> None:
        self._path = relay_dir / STORE_FILE
        self._relay_id = relay_id
        self._clock = clock
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._by_channel: dict[str, list[str]] = {}  # channel monitors only: channel -> record ids
        self._flush_task: asyncio.Task | None = None

    def load(self) -> None:
        data = load_json(self._path)
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        for rec in data.get("subscriptions") or []:
            if isinstance(rec, dict) and rec.get("id"):
                self._records[rec["id"]] = rec
        self._reindex()

    def _reindex(self) -> None:
        index: dict[str, list[str]] = {}
        for rid, r in self._records.items():
            if r.get("kind") in MONITOR_KINDS and r.get("channel"):
                index.setdefault(r["channel"], []).append(rid)
        self._by_channel = index

    async def _persist(self) -> None:
        self._reindex()
        payload = {"version": 1, "subscriptions": list(self._records.values())}
        try:
            await asyncio.to_thread(atomic_write_json, self._path, payload)
        except OSError:
            logger.exception("Could not write relay subscriptions")

    def _persist_soon(self) -> None:
        """Debounced write for cursor / counter updates."""
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def later() -> None:
            await asyncio.sleep(STATE_FLUSH_DEBOUNCE_SECONDS)
            async with self._lock:
                await self._persist()

        self._flush_task = loop.create_task(later())

    async def close(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            await asyncio.gather(self._flush_task, return_exceptions=True)
        async with self._lock:
            await self._persist()

    # ------------------------------------------------- SubscriptionStore API

    async def list(self, relay_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(r) for r in self._records.values()
            if r.get("relay_id") == relay_id and (session_id is None or r.get("session_id") == session_id)
        ]

    async def get(self, relay_id: str, subscription_id: str) -> dict[str, Any] | None:
        rec = self._records.get(subscription_id)
        return dict(rec) if rec and rec.get("relay_id") == relay_id else None

    async def add(self, relay_id: str, subscription: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            rec = {**subscription, "id": subscription.get("id") or str(uuid.uuid4()),
                   "relay_id": relay_id}
            self._records[rec["id"]] = rec
            await self._persist()
            return dict(rec)

    async def remove(self, relay_id: str, subscription_id: str) -> bool:
        async with self._lock:
            rec = self._records.get(subscription_id)
            if rec is None or rec.get("relay_id") != relay_id:
                return False
            del self._records[subscription_id]
            await self._persist()
            return True

    async def update_last_seen(self, relay_id: str, subscription_id: str, ts: str) -> None:
        async with self._lock:
            rec = self._records.get(subscription_id)
            if rec is not None and rec.get("relay_id") == relay_id:
                rec["last_seen_ts"] = ts
                await self._persist()

    async def update(self, relay_id: str, subscription_id: str, **fields: Any) -> dict[str, Any] | None:
        async with self._lock:
            rec = self._records.get(subscription_id)
            if rec is None or rec.get("relay_id") != relay_id:
                return None
            rec.update(fields)
            await self._persist()
            return dict(rec)

    # ------------------------------------------------------- session / threads

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._records.values() if r.get("session_id") == session_id]

    async def remove_by_session(self, session_id: str) -> int:
        async with self._lock:
            ids = [i for i, r in self._records.items() if r.get("session_id") == session_id]
            for i in ids:
                del self._records[i]
            if ids:
                await self._persist()
            return len(ids)

    async def set_status_by_session(
        self, session_id: str, status: str, *, reason: str | None = None,
        only_reason: str | None = None, only_status: str | None = None,
        kinds: frozenset[str] | None = None,
    ) -> int:
        """Set status on a session's records (never touches the owner ``paused`` flag).
        ``only_reason`` restricts to records suspended for that reason (restoring dispose-
        suspended records on start); ``only_status`` to records in that status (dispose suspends
        only ACTIVE ones, so another suspension reason is not overwritten); ``kinds`` to those
        record kinds."""
        async with self._lock:
            n = 0
            for r in self._records.values():
                if r.get("session_id") != session_id:
                    continue
                if kinds is not None and r.get("kind") not in kinds:
                    continue
                if only_reason is not None and r.get("suspended_reason") != only_reason:
                    continue
                if only_status is not None and r.get("status") != only_status:
                    continue
                r["status"] = status
                r["suspended_reason"] = reason if status == SUSPENDED else None
                r["notified_inactive"] = False
                n += 1
            if n:
                await self._persist()
            return n

    def find_thread(self, channel: str, thread_ts: str) -> dict[str, Any] | None:
        """The (active or suspended) instruction-thread record for a Slack thread, or None."""
        for r in self._records.values():
            if (r.get("kind") == THREAD_KIND and r.get("channel") == channel
                    and r.get("thread_ts") == thread_ts):
                return dict(r)
        return None

    def has_active_thread(self, session_id: str, channel: str, thread_ts: str) -> bool:
        """Sync lookup for the gate's thread exception: an ACTIVE record of this thread owned by
        this session."""
        for r in self._records.values():
            if (r.get("kind") == THREAD_KIND and r.get("status") == ACTIVE
                    and r.get("session_id") == session_id and r.get("channel") == channel
                    and r.get("thread_ts") == thread_ts):
                return True
        return False

    async def create_thread(
        self, session_id: str, channel: str, thread_ts: str, origin_ts: str
    ) -> dict[str, Any]:
        """Create (or return) the instruction-thread record. Only ever called by the inbound
        pipeline for a validated owner top-level instruction: ``origin_ts`` must be the thread
        root itself."""
        if origin_ts != thread_ts:
            raise ValueError("a thread record is created only from its own root message")
        existing = self.find_thread(channel, thread_ts)
        if existing is not None:
            return existing
        now = self._clock()
        rec = {
            "id": str(uuid.uuid4()), "relay_id": self._relay_id, "kind": THREAD_KIND,
            "session_id": session_id, "created_by": "owner_instruction", "channel": channel,
            "thread_ts": thread_ts, "status": ACTIVE, "suspended_reason": None,
            "last_seen_ts": thread_ts, "created_at": now, "last_activity_at": now,
            "origin_ts": origin_ts, "notified_inactive": False,
        }
        return await self.add(self._relay_id, rec)

    async def touch_thread(self, record_id: str, *, last_seen_ts: str | None = None) -> None:
        async with self._lock:
            rec = self._records.get(record_id)
            if rec is None:
                return
            rec["last_activity_at"] = self._clock()
            if last_seen_ts and _newer(last_seen_ts, rec.get("last_seen_ts")):
                rec["last_seen_ts"] = last_seen_ts
            await self._persist()

    async def housekeeping(self) -> tuple[int, int]:
        """Expire records idle past the TTL, then enforce the cap (oldest idle first).
        Returns (expired, evicted)."""
        async with self._lock:
            now = self._clock()
            expired = [
                i for i, r in self._records.items()
                if r.get("kind") == THREAD_KIND
                and now - float(r.get("last_activity_at") or 0) > THREAD_TTL_SECONDS
            ]
            for i in expired:
                del self._records[i]
            threads = sorted(
                (r for r in self._records.values() if r.get("kind") == THREAD_KIND),
                key=lambda r: float(r.get("last_activity_at") or 0),
            )
            evicted = 0
            while len(threads) > MAX_THREAD_RECORDS:
                victim = threads.pop(0)
                del self._records[victim["id"]]
                evicted += 1
            if expired or evicted:
                await self._persist()
            return len(expired), evicted


    # ----------------------------------------------------------------- monitors (3b-i)

    @staticmethod
    def _ts(now: float) -> str:
        return f"{now:.6f}"

    def monitors(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Monitor records (live references: callers must not mutate them)."""
        return [
            r for r in self._records.values()
            if r.get("kind") in MONITOR_KINDS and (session_id is None or r.get("session_id") == session_id)
        ]

    def scope_monitors(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Mentions / DMs monitors (no channel): small in-memory list, no I/O."""
        return [
            r for r in self._records.values()
            if r.get("kind") in const.SCOPE_KINDS and (kind is None or r.get("kind") == kind)
        ]

    def monitors_for_channel(self, channel: str) -> list[dict[str, Any]]:
        """Index lookup used by the event handler (never a full scan)."""
        return [self._records[i] for i in self._by_channel.get(channel, ()) if i in self._records]

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        rec = self._records.get(monitor_id)
        return rec if rec is not None and rec.get("kind") in MONITOR_KINDS else None

    def count(self, session_id: str | None = None) -> int:
        return len(self.monitors(session_id))

    def find_monitor(
        self, session_id: str, kind: str, channel: str | None, thread_ts: str | None,
        filters: dict[str, Any],
    ) -> dict[str, Any] | None:
        for r in self.monitors(session_id):
            if (r["kind"] == kind and r.get("channel") == channel and r.get("thread_ts") == thread_ts
                    and r.get("filters") == filters):
                return r
        return None

    async def create_monitor(
        self, session_id: str, kind: str, channel: str | None, *, thread_ts: str | None = None,
        filters: dict[str, Any] | None = None, expires_at: float | None = None,
        created_by: str = "agent", consent: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Create a monitor, or return the identical existing one. Returns ``(record, outcome)``
        with outcome ``created`` | ``existing`` | ``resumed`` (a flood / delivery-failed monitor
        re-created by the agent starts again from now). Caps are checked under the lock."""
        if kind not in MONITOR_KINDS:
            raise ValueError(f"unknown monitor kind {kind!r}")
        filters = filters or {"keywords": [], "users": []}
        async with self._lock:
            existing = self.find_monitor(session_id, kind, channel, thread_ts, filters)
            now = self._clock()
            if existing is not None:
                if existing["status"] == SUSPENDED and existing.get("suspended_reason") in (
                    const.SUSPEND_FLOOD, const.SUSPEND_DELIVERY_FAILED
                ):
                    existing.update(
                        status=ACTIVE, suspended_reason=None, last_seen_ts=self._ts(now),
                        last_error=None, failures=0, flood_windows=0,
                    )
                    await self._persist()
                    return existing, "resumed"
                return existing, "existing"
            if self.count(session_id) >= const.MAX_MONITORS_PER_SESSION or (
                self.count() >= const.MAX_MONITORS_PER_RELAY
            ):
                raise MonitorLimitError()
            rec = {
                "id": str(uuid.uuid4()), "relay_id": self._relay_id, "kind": kind,
                "session_id": session_id, "created_by": created_by, "created_at": now,
                "expires_at": expires_at, "status": ACTIVE, "suspended_reason": None,
                "paused": False, "channel": channel, "thread_ts": thread_ts, "filters": filters,
                "last_seen_ts": self._ts(now), "last_activity_at": now, "last_delivered_at": None,
                "last_error": None, "failures": 0, "flood_windows": 0,
                "counters": {"delivered": 0, "dropped": 0, "coalesced": 0},
                "recent_keys": [],
            }
            if consent is not None:
                rec["consent"] = consent  # written only after an approved scope prompt
            self._records[rec["id"]] = rec
            await self._persist()
            return rec, "created"

    async def set_monitor_status(
        self, monitor_id: str, status: str, reason: str | None = None, *, resume_from: float | None = None
    ) -> dict[str, Any] | None:
        """Suspend / resume one monitor. A resume with ``resume_from`` restarts the durable cursor
        there (policy resume starts from now, D4)."""
        async with self._lock:
            rec = self.get_monitor(monitor_id)
            if rec is None:
                return None
            rec["status"] = status
            rec["suspended_reason"] = reason if status == SUSPENDED else None
            if status == ACTIVE:
                rec["failures"] = 0
                rec["flood_windows"] = 0
                if resume_from is not None:
                    rec["last_seen_ts"] = self._ts(resume_from)
            await self._persist()
            return rec

    async def set_paused(
        self, monitor_id: str, paused: bool, *, resume_from: float | None = None
    ) -> dict[str, Any] | None:
        """Owner pause / resume. A resume with ``resume_from`` restarts the durable cursor there
        (the paused period is not backfilled). Returns the record, or None when unknown."""
        async with self._lock:
            rec = self.get_monitor(monitor_id)
            if rec is None:
                return None
            rec["paused"] = bool(paused)
            if not paused and resume_from is not None:
                rec["last_seen_ts"] = self._ts(resume_from)
                rec["recent_keys"] = []
            await self._persist()
            return rec

    def advance_cursor(self, monitor_id: str, ts: str) -> bool:
        """Monotonic durable cursor (debounced write)."""
        rec = self.get_monitor(monitor_id)
        if rec is None or not _newer(ts, rec.get("last_seen_ts")):
            return False
        rec["last_seen_ts"] = ts
        self._persist_soon()
        return True

    def note_delivered_keys(self, monitor_id: str, keys: list[str]) -> None:
        """Persist the newest delivered message keys of a monitor (bounded) together with the cursor
        advance: the 60 s backfill margin must not repeat them after a restart."""
        rec = self.get_monitor(monitor_id)
        if rec is None or not keys:
            return
        merged = [k for k in rec.get("recent_keys") or [] if k not in keys] + list(keys)
        rec["recent_keys"] = merged[-const.RECENT_KEYS_PERSISTED:]
        self._persist_soon()

    def bump_counters(self, monitor_id: str, **deltas: int) -> None:
        rec = self.get_monitor(monitor_id)
        if rec is None:
            return
        counters = rec.setdefault("counters", {})
        for key, delta in deltas.items():
            counters[key] = int(counters.get(key, 0)) + delta
        self._persist_soon()

    def touch_monitor(self, monitor_id: str, **fields: Any) -> None:
        """Update bookkeeping fields (last_activity_at, last_delivered_at, last_error, failures,
        flood_windows); debounced."""
        rec = self.get_monitor(monitor_id)
        if rec is None:
            return
        rec.update(fields)
        self._persist_soon()

    async def remove_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        async with self._lock:
            if self.get_monitor(monitor_id) is None:
                return None
            rec = self._records.pop(monitor_id)
            await self._persist()
            return rec

    async def expire_monitors(self) -> list[dict[str, Any]]:
        """Remove monitors past ``expires_at`` and thread monitors idle past the TTL."""
        async with self._lock:
            now = self._clock()
            gone = []
            for r in list(self.monitors()):
                expired = r.get("expires_at") is not None and now >= float(r["expires_at"])
                idle = (r["kind"] == const.KIND_MONITOR_THREAD
                        and now - float(r.get("last_activity_at") or 0) > const.MONITOR_THREAD_IDLE_SECONDS)
                if expired or idle:
                    gone.append({**self._records.pop(r["id"]), "_expired_reason": "expired" if expired else "idle"})
            if gone:
                await self._persist()
            return gone


def _newer(a: str, b: str | None) -> bool:
    try:
        return float(a) > float(b or 0)
    except (TypeError, ValueError):
        return False
