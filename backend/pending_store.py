"""
Pending-Set Data Layer for Claude Code WebUI (issue #2193, AC10)

Per-session ledger of pending `Comm` deliveries, with JSONL persistence mirroring
`QueueManager`'s proven replay pattern. A `PendingItem` wraps one full `Comm` plus
delivery-mechanics fields (status, position, timestamps, error) that are about
*how* an entry is delivered, not what it contains.

Pure data layer: nothing in production wires this up yet. `QueueManager`/
`queue.jsonl` remain the real ledger for production queue traffic until a later
stage (issue #2193's staged rollout) cuts the REST endpoints over.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from .models.legion_models import Comm
from .timestamp_utils import get_unix_timestamp

pending_logger = get_logger('queue_manager', category='PENDING')
logger = logging.getLogger(__name__)

_TERMINAL_STATUSES: frozenset[str] = frozenset({"delivered", "failed", "cancelled"})


@dataclass
class PendingItem:
    """A pending Comm delivery awaiting dispatch to its recipient session."""
    comm: Comm
    session_id: str  # recipient; must equal comm.to_minion_id
    status: str = "pending"  # pending | delivered | failed | cancelled
    position: int = 0
    created_at: float = 0.0
    delivered_at: float | None = None
    error: str | None = None
    inject_timestamp: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "comm": self.comm.to_dict(),
            "session_id": self.session_id,
            "status": self.status,
            "position": self.position,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "error": self.error,
            "inject_timestamp": self.inject_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingItem":
        data = data.copy()
        data["comm"] = Comm.from_dict(data["comm"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class PendingStore:
    """
    Manages per-session pending-delivery ledgers with JSONL persistence.

    Storage format (pending.jsonl):
      {"type":"enqueue","comm":{...},"session_id":"...","position":...,"created_at":...,"inject_timestamp":...}
      {"type":"status","comm_id":"...","status":"delivered","delivered_at":...}
      {"type":"status","comm_id":"...","status":"cancelled"}
      {"type":"history_cleared","cleared_at":...}

    State is rebuilt by replaying the log via load().
    """

    def __init__(self):
        # session_id -> list of PendingItem (ordered by position)
        self._pending: dict[str, list[PendingItem]] = {}

    def _get_ledger_file(self, session_dir: Path) -> Path:
        return session_dir / "pending.jsonl"

    # =========================================================================
    # Persistence
    # =========================================================================

    async def _append_entry(self, session_dir: Path, entry: dict) -> None:
        """Append a JSONL entry to the ledger file."""
        ledger_file = self._get_ledger_file(session_dir)
        try:
            with open(ledger_file, 'a', encoding='utf-8') as f:
                json.dump(entry, f, ensure_ascii=False)
                f.write('\n')
        except Exception as e:
            logger.error(f"Failed to append pending entry: {e}")
            raise

    async def load(self, session_id: str, session_dir: Path) -> list[PendingItem]:
        """
        Replay pending.jsonl to rebuild in-memory state for a session.

        Returns list of all items (including terminal states for history).
        Terminal items whose created_at <= cleared_at_max (from history_cleared
        markers) are dropped.
        """
        ledger_file = self._get_ledger_file(session_dir)
        items_by_id: dict[str, PendingItem] = {}
        cleared_at_max: float = 0.0

        if not ledger_file.exists():
            self._pending[session_id] = []
            return []

        try:
            with open(ledger_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping malformed pending entry: {e}")
                        continue

                    entry_type = entry.get("type")

                    if entry_type == "history_cleared":
                        ts = entry.get("cleared_at", 0.0)
                        if ts > cleared_at_max:
                            cleared_at_max = ts

                    elif entry_type == "enqueue":
                        comm = Comm.from_dict(entry["comm"])
                        item = PendingItem(
                            comm=comm,
                            session_id=entry.get("session_id", session_id),
                            status="pending",
                            position=entry.get("position", 0),
                            created_at=entry.get("created_at", 0.0),
                            inject_timestamp=entry.get("inject_timestamp", True),
                        )
                        items_by_id[comm.comm_id] = item

                    elif entry_type == "status":
                        comm_id = entry.get("comm_id")
                        if comm_id in items_by_id:
                            item = items_by_id[comm_id]
                            item.status = entry.get("status", item.status)
                            if "delivered_at" in entry:
                                item.delivered_at = entry["delivered_at"]
                            if "error" in entry:
                                item.error = entry["error"]

        except Exception as e:
            logger.error(f"Failed to load pending store for session {session_id}: {e}")

        all_items = [
            i for i in items_by_id.values()
            if not (i.status in _TERMINAL_STATUSES and cleared_at_max > 0 and i.created_at <= cleared_at_max)
        ]
        all_items.sort(key=lambda x: x.position)
        self._pending[session_id] = all_items

        pending_count = sum(1 for i in all_items if i.status == "pending")
        if pending_count:
            pending_logger.info(
                f"Loaded pending store for session {session_id}: {pending_count} pending, {len(all_items)} total"
            )

        return all_items

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    async def enqueue(
        self,
        session_id: str,
        session_dir: Path,
        comm: Comm,
        *,
        inject_timestamp: bool = True,
        max_pending_size: int = 100,
    ) -> PendingItem:
        """Add a Comm to the session's pending-delivery ledger."""
        comm.validate()
        if comm.to_minion_id != session_id:
            raise ValueError(
                f"Comm.to_minion_id ({comm.to_minion_id!r}) does not match session_id ({session_id!r})"
            )

        pending_items = self._pending.get(session_id, [])
        pending = [i for i in pending_items if i.status == "pending"]

        if len(pending) >= max_pending_size:
            raise ValueError(f"Pending store full: {len(pending)}/{max_pending_size} pending items")

        # Determine next position (after highest existing pending position)
        next_pos = max((i.position for i in pending), default=-1) + 1

        item = PendingItem(
            comm=comm,
            session_id=session_id,
            status="pending",
            position=next_pos,
            created_at=get_unix_timestamp(),
            inject_timestamp=inject_timestamp,
        )

        pending_items.append(item)
        self._pending[session_id] = pending_items

        await self._append_entry(session_dir, {
            "type": "enqueue",
            "comm": item.comm.to_dict(),
            "session_id": item.session_id,
            "position": item.position,
            "created_at": item.created_at,
            "inject_timestamp": item.inject_timestamp,
        })

        pending_logger.info(f"Enqueued {item.comm.comm_id} for session {session_id} at position {next_pos}")
        return item

    async def mark_delivered(self, session_id: str, session_dir: Path, comm_id: str) -> PendingItem | None:
        """Mark a pending item as delivered."""
        item = self._find_item(session_id, comm_id)
        if not item:
            return None

        item.status = "delivered"
        item.delivered_at = get_unix_timestamp()
        await self._append_entry(session_dir, {
            "type": "status",
            "comm_id": comm_id,
            "status": "delivered",
            "delivered_at": item.delivered_at,
        })

        pending_logger.info(f"Marked {comm_id} as delivered for session {session_id}")
        return item

    async def mark_failed(
        self, session_id: str, session_dir: Path, comm_id: str, error: str
    ) -> PendingItem | None:
        """Mark a pending item as failed."""
        item = self._find_item(session_id, comm_id)
        if not item:
            return None

        item.status = "failed"
        item.error = error
        await self._append_entry(session_dir, {
            "type": "status",
            "comm_id": comm_id,
            "status": "failed",
            "error": error,
        })

        pending_logger.info(f"Marked {comm_id} as failed for session {session_id}: {error}")
        return item

    async def cancel(self, session_id: str, session_dir: Path, comm_id: str) -> PendingItem | None:
        """Cancel a pending item (pending-only guard)."""
        item = self._find_item(session_id, comm_id)
        if not item or item.status != "pending":
            return None

        item.status = "cancelled"
        await self._append_entry(session_dir, {
            "type": "status",
            "comm_id": comm_id,
            "status": "cancelled",
        })

        pending_logger.info(f"Cancelled {comm_id} for session {session_id}")
        return item

    async def clear_pending(self, session_id: str, session_dir: Path) -> int:
        """Cancel all pending items. Returns count of cancelled items."""
        items = self._pending.get(session_id, [])
        count = 0
        for item in items:
            if item.status == "pending":
                item.status = "cancelled"
                await self._append_entry(session_dir, {
                    "type": "status",
                    "comm_id": item.comm.comm_id,
                    "status": "cancelled",
                })
                count += 1

        if count:
            pending_logger.info(f"Cleared {count} pending items for session {session_id}")
        return count

    async def clear_history(self, session_id: str, session_dir: Path) -> int:
        """
        Remove terminal (delivered/failed/cancelled) items from the ledger.

        Appends a history_cleared marker to pending.jsonl so that load() will
        drop these items on replay. Does not affect pending items.
        Returns count of removed items.
        """
        items = self._pending.get(session_id, [])
        terminal_items = [i for i in items if i.status in _TERMINAL_STATUSES]
        if not terminal_items:
            return 0

        cleared_at = max(i.created_at for i in terminal_items)
        await self._append_entry(session_dir, {
            "type": "history_cleared",
            "cleared_at": cleared_at,
        })

        self._pending[session_id] = [i for i in items if i.status not in _TERMINAL_STATUSES]
        count = len(terminal_items)
        pending_logger.info(f"Cleared history ({count} terminal items) for session {session_id}")
        return count

    # =========================================================================
    # Query
    # =========================================================================

    def get_all(self, session_id: str) -> list[PendingItem]:
        """Get all items for a session, sorted by position (full ledger incl. terminal)."""
        items = self._pending.get(session_id, [])
        return sorted(items, key=lambda x: x.position)

    def get_page(self, session_id: str, limit: int = 100, offset: int = 0) -> dict:
        """Get ledger items for a session, paginated, most recent first."""
        all_items = [item.to_dict() for item in self.get_all(session_id)]
        all_items.reverse()  # Most recent (highest position) first
        total = len(all_items)
        pending_count = sum(1 for i in all_items if i.get("status") == "pending")
        sliced = all_items[offset : offset + limit]
        return {
            "items": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
            "pending_count": pending_count,
        }

    def get_pending(self, session_id: str) -> list[PendingItem]:
        """Get pending items sorted by position (FIFO)."""
        return [i for i in self.get_all(session_id) if i.status == "pending"]

    def peek_next(self, session_id: str) -> PendingItem | None:
        """Get the next pending item without removing it."""
        pending = self.get_pending(session_id)
        return pending[0] if pending else None

    def _find_item(self, session_id: str, comm_id: str) -> PendingItem | None:
        """Find a pending item by comm_id."""
        for item in self._pending.get(session_id, []):
            if item.comm.comm_id == comm_id:
                return item
        return None

    # =========================================================================
    # Cleanup
    # =========================================================================

    def remove_session(self, session_id: str) -> None:
        """Remove in-memory pending state for a session."""
        self._pending.pop(session_id, None)
