"""Relay-level audit log (issue #2103).

Append-only JSONL at ``data/relays/{relay_id}/audit.jsonl``, size-rotated. Records
writes (post/reply/upload/react/mark_read), denied attempts and policy changes for
attribution and debugging. Separate from the session tool-call audit. Only the
whitelisted fields below are ever written: no tokens, no full message text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUDIT_FILE = "audit.jsonl"
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
TEXT_PREVIEW_LEN = 200
MAX_QUERY_LIMIT = 500

_RELAY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

_FIELDS = (
    "ts", "event", "session_id", "handle", "session_name", "tool", "channel_id", "channel_name",
    "thread_ts", "message_ts", "outcome", "code", "policy", "approval", "text_sha256",
    "text_preview",
)


def text_fields(text: str | None) -> dict[str, str | None]:
    """sha256 + bounded preview for outbound text."""
    if not text:
        return {"text_sha256": None, "text_preview": None}
    return {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_preview": text[:TEXT_PREVIEW_LEN],
    }


class RelayAuditLog:
    def __init__(self, data_dir: Path, *, max_bytes: int = MAX_BYTES, backups: int = BACKUPS):
        self._root = Path(data_dir) / "relays"
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = threading.Lock()

    def _path(self, relay_id: str) -> Path:
        if not _RELAY_ID_RE.match(relay_id or ""):
            raise ValueError("invalid relay id")
        return self._root / relay_id / AUDIT_FILE

    def _rotate(self, path: Path) -> None:
        for i in range(self._backups, 0, -1):
            src = path if i == 1 else path.with_name(f"{AUDIT_FILE}.{i - 1}")
            dst = path.with_name(f"{AUDIT_FILE}.{i}")
            if src.exists():
                if i == self._backups and dst.exists():
                    dst.unlink()
                src.replace(dst)

    def _append_sync(self, relay_id: str, record: dict[str, Any]) -> None:
        path = self._path(relay_id)
        line = json.dumps({k: record.get(k) for k in _FIELDS}, ensure_ascii=False) + "\n"
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(line.encode()) > self._max_bytes:
                self._rotate(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)

    async def record(self, relay_id: str, **fields: Any) -> None:
        """Append one record. Never raises: audit failure must not break a relay call."""
        fields.setdefault("ts", datetime.now(UTC).isoformat())
        fields.setdefault("event", "tool")
        try:
            await asyncio.to_thread(self._append_sync, relay_id, fields)
        except Exception:
            logger.exception("Failed to write relay audit record")

    def _query_sync(
        self, relay_id: str, session_id: str | None, channel: str | None, limit: int,
        before: str | None,
    ) -> list[dict]:
        path = self._path(relay_id)
        files = [path] + [path.with_name(f"{AUDIT_FILE}.{i}") for i in range(1, self._backups + 1)]
        limit = max(1, min(limit, MAX_QUERY_LIMIT))
        out: list[dict] = []
        with self._lock:
            for f in files:  # newest file first
                if not f.exists():
                    continue
                for line in reversed(f.read_text(encoding="utf-8").splitlines()):
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if session_id and rec.get("session_id") != session_id:
                        continue
                    if channel and channel not in (rec.get("channel_id"), rec.get("channel_name")):
                        continue
                    if before and (rec.get("ts") or "") >= before:
                        continue
                    out.append(rec)
                    if len(out) >= limit:
                        return out
        return out

    async def query(
        self, relay_id: str, *, session_id: str | None = None, channel: str | None = None,
        limit: int = 100, before: str | None = None,
    ) -> list[dict]:
        """Newest-first records; ``before`` is an ISO ``ts`` cursor (exclusive)."""
        return await asyncio.to_thread(self._query_sync, relay_id, session_id, channel, limit, before)
