"""
Message Queue Manager for Claude Code WebUI

Manages queued messages for sessions with JSONL persistence.
Queue items are delivered sequentially by QueueProcessor.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .timestamp_utils import get_unix_timestamp

queue_logger = get_logger('queue_manager', category='QUEUE')
logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """A queued message waiting to be delivered to a session."""
    queue_id: str
    session_id: str
    content: str
    reset_session: bool = True
    metadata: dict | None = None
    status: str = "pending"  # pending | sent | failed | cancelled
    position: int = 0
    created_at: float = 0.0
    sent_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueItem":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class QueueManager:
    """
    Manages per-session message queues with JSONL persistence.

    Storage format (queue.jsonl):
      {"type":"enqueue","queue_id":"...","content":"...","reset_session":true,...}
      {"type":"status","queue_id":"...","status":"sent","sent_at":...}
      {"type":"status","queue_id":"...","status":"cancelled"}

    State is rebuilt by replaying the log on startup.
    """

    def __init__(self):
        # session_id -> list of QueueItem (ordered by position)
        self._queues: dict[str, list[QueueItem]] = {}

    def _get_queue_file(self, session_dir: Path) -> Path:
        return session_dir / "queue.jsonl"

    # =========================================================================
    # Persistence
    # =========================================================================

    async def _append_entry(self, session_dir: Path, entry: dict) -> None:
        """Append a JSONL entry to the queue file."""
        queue_file = self._get_queue_file(session_dir)
        try:
            with open(queue_file, 'a', encoding='utf-8') as f:
                json.dump(entry, f, ensure_ascii=False)
                f.write('\n')
        except Exception as e:
            logger.error(f"Failed to append queue entry: {e}")
            raise

    async def load_queue(self, session_id: str, session_dir: Path) -> list[QueueItem]:
        """
        Replay queue.jsonl to rebuild in-memory state for a session.

        Returns list of all items (including terminal states for history).
        """
        queue_file = self._get_queue_file(session_dir)
        items_by_id: dict[str, QueueItem] = {}

        if not queue_file.exists():
            self._queues[session_id] = []
            return []

        try:
            with open(queue_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping malformed queue entry: {e}")
                        continue

                    entry_type = entry.get("type")
                    queue_id = entry.get("queue_id")

                    if entry_type == "enqueue":
                        item = QueueItem(
                            queue_id=queue_id,
                            session_id=session_id,
                            content=entry.get("content", ""),
                            reset_session=entry.get("reset_session", True),
                            metadata=entry.get("metadata"),
                            status="pending",
                            position=entry.get("position", 0),
                            created_at=entry.get("created_at", 0.0),
                        )
                        items_by_id[queue_id] = item

                    elif entry_type == "status" and queue_id in items_by_id:
                        item = items_by_id[queue_id]
                        item.status = entry.get("status", item.status)
                        if "sent_at" in entry:
                            item.sent_at = entry["sent_at"]
                        if "error" in entry:
                            item.error = entry["error"]

        except Exception as e:
            logger.error(f"Failed to load queue for session {session_id}: {e}")

        all_items = list(items_by_id.values())
        all_items.sort(key=lambda x: x.position)
        self._queues[session_id] = all_items

        pending_count = sum(1 for i in all_items if i.status == "pending")
        if pending_count:
            queue_logger.info(f"Loaded queue for session {session_id}: {pending_count} pending, {len(all_items)} total")

        return all_items

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    async def enqueue(
        self,
        session_id: str,
        session_dir: Path,
        content: str,
        reset_session: bool = True,
        metadata: dict | None = None,
        max_queue_size: int = 100,
    ) -> QueueItem:
        """Add a message to the session's queue."""
        queue = self._queues.get(session_id, [])
        pending = [i for i in queue if i.status == "pending"]

        if len(pending) >= max_queue_size:
            raise ValueError(f"Queue full: {len(pending)}/{max_queue_size} pending items")

        # Determine next position (after highest existing pending position)
        next_pos = max((i.position for i in pending), default=-1) + 1

        item = QueueItem(
            queue_id=str(uuid.uuid4()),
            session_id=session_id,
            content=content,
            reset_session=reset_session,
            metadata=metadata,
            status="pending",
            position=next_pos,
            created_at=get_unix_timestamp(),
        )

        queue.append(item)
        self._queues[session_id] = queue

        await self._append_entry(session_dir, {
            "type": "enqueue",
            "queue_id": item.queue_id,
            "content": item.content,
            "reset_session": item.reset_session,
            "metadata": item.metadata,
            "position": item.position,
            "created_at": item.created_at,
        })

        queue_logger.info(f"Enqueued {item.queue_id} for session {session_id} at position {next_pos}")
        return item

    async def cancel(self, session_id: str, session_dir: Path, queue_id: str) -> QueueItem | None:
        """Cancel a pending queue item."""
        item = self._find_item(session_id, queue_id)
        if not item or item.status != "pending":
            return None

        item.status = "cancelled"
        await self._append_entry(session_dir, {
            "type": "status",
            "queue_id": queue_id,
            "status": "cancelled",
        })

        queue_logger.info(f"Cancelled queue item {queue_id} for session {session_id}")
        return item

    async def mark_sent(self, session_id: str, session_dir: Path, queue_id: str) -> QueueItem | None:
        """Mark a queue item as sent."""
        item = self._find_item(session_id, queue_id)
        if not item:
            return None

        item.status = "sent"
        item.sent_at = get_unix_timestamp()
        await self._append_entry(session_dir, {
            "type": "status",
            "queue_id": queue_id,
            "status": "sent",
            "sent_at": item.sent_at,
        })

        queue_logger.info(f"Marked queue item {queue_id} as sent for session {session_id}")
        return item

    async def mark_failed(
        self, session_id: str, session_dir: Path, queue_id: str, error: str
    ) -> QueueItem | None:
        """Mark a queue item as failed."""
        item = self._find_item(session_id, queue_id)
        if not item:
            return None

        item.status = "failed"
        item.error = error
        await self._append_entry(session_dir, {
            "type": "status",
            "queue_id": queue_id,
            "status": "failed",
            "error": error,
        })

        queue_logger.info(f"Marked queue item {queue_id} as failed for session {session_id}: {error}")
        return item

    async def requeue(
        self, session_id: str, session_dir: Path, queue_id: str
    ) -> QueueItem | None:
        """
        Re-queue a sent or failed item by creating a new pending item
        at the front of the queue. Original item remains unchanged.
        """
        original = self._find_item(session_id, queue_id)
        if not original or original.status not in ("sent", "failed"):
            return None

        # Insert at front: position -1 relative to current lowest pending
        queue = self._queues.get(session_id, [])
        pending = [i for i in queue if i.status == "pending"]
        front_pos = min((i.position for i in pending), default=0) - 1

        new_item = await self.enqueue(
            session_id=session_id,
            session_dir=session_dir,
            content=original.content,
            reset_session=original.reset_session,
            metadata=original.metadata,
        )
        # Override position to be at the front
        new_item.position = front_pos
        # Re-persist with corrected position (the enqueue entry already written
        # will have a higher position, but load_queue sorts by position so this
        # works correctly on replay). For exactness, write a position override.
        await self._append_entry(session_dir, {
            "type": "status",
            "queue_id": new_item.queue_id,
            "status": "pending",
            "position": front_pos,
        })

        queue_logger.info(
            f"Re-queued {original.queue_id} as {new_item.queue_id} at front for session {session_id}"
        )
        return new_item

    async def clear_pending(self, session_id: str, session_dir: Path) -> int:
        """Cancel all pending items. Returns count of cancelled items."""
        queue = self._queues.get(session_id, [])
        count = 0
        for item in queue:
            if item.status == "pending":
                item.status = "cancelled"
                await self._append_entry(session_dir, {
                    "type": "status",
                    "queue_id": item.queue_id,
                    "status": "cancelled",
                })
                count += 1

        if count:
            queue_logger.info(f"Cleared {count} pending items for session {session_id}")
        return count

    # =========================================================================
    # Query
    # =========================================================================

    def get_queue(self, session_id: str) -> list[QueueItem]:
        """Get all queue items for a session, sorted by position."""
        items = self._queues.get(session_id, [])
        return sorted(items, key=lambda x: x.position)

    def get_pending(self, session_id: str) -> list[QueueItem]:
        """Get pending items sorted by position (FIFO)."""
        return [i for i in self.get_queue(session_id) if i.status == "pending"]

    def get_pending_count(self, session_id: str) -> int:
        """Get count of pending items."""
        return len(self.get_pending(session_id))

    def peek_next(self, session_id: str) -> QueueItem | None:
        """Get the next pending item without removing it."""
        pending = self.get_pending(session_id)
        return pending[0] if pending else None

    def _find_item(self, session_id: str, queue_id: str) -> QueueItem | None:
        """Find a queue item by ID."""
        for item in self._queues.get(session_id, []):
            if item.queue_id == queue_id:
                return item
        return None

    # =========================================================================
    # Cleanup
    # =========================================================================

    def remove_session(self, session_id: str) -> None:
        """Remove in-memory queue state for a session."""
        self._queues.pop(session_id, None)
