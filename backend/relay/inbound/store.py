"""JsonSubscriptionStore: durable subscription / thread records (issue #2103 stage 3a, D7).

``data/relays/{relay_id}/subscriptions.json`` = ``{"version":1,"subscriptions":[...]}``. 3a only
writes ``kind="instruction_thread"`` records; 3b adds kinds on the same storage. The in-memory
index is the read path (thread-grant lookups are synchronous); every mutation writes through.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from .constants import MAX_THREAD_RECORDS, THREAD_KIND, THREAD_TTL_SECONDS
from .persist import atomic_write_json, load_json

logger = logging.getLogger(__name__)

STORE_FILE = "subscriptions.json"
ACTIVE = "active"
SUSPENDED = "suspended"


class JsonSubscriptionStore:
    def __init__(self, relay_dir: Path, relay_id: str, *, clock=time.time) -> None:
        self._path = relay_dir / STORE_FILE
        self._relay_id = relay_id
        self._clock = clock
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def load(self) -> None:
        data = load_json(self._path)
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        for rec in data.get("subscriptions") or []:
            if isinstance(rec, dict) and rec.get("id"):
                self._records[rec["id"]] = rec

    async def _persist(self) -> None:
        payload = {"version": 1, "subscriptions": list(self._records.values())}
        try:
            await asyncio.to_thread(atomic_write_json, self._path, payload)
        except OSError:
            logger.exception("Could not write relay subscriptions")

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
        only_reason: str | None = None,
    ) -> int:
        """Set status on a session's records; ``only_reason`` restricts to records suspended for
        that reason (used to restore dispose-suspended records on start)."""
        async with self._lock:
            n = 0
            for r in self._records.values():
                if r.get("session_id") != session_id:
                    continue
                if only_reason is not None and r.get("suspended_reason") != only_reason:
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


def _newer(a: str, b: str | None) -> bool:
    try:
        return float(a) > float(b or 0)
    except (TypeError, ValueError):
        return False
