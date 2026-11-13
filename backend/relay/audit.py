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
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
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
    "thread_ts", "message_ts", "outcome", "code", "policy", "approval", "prompt_via", "text_sha256",
    "text_preview", "query_sha256", "query_preview", "kinds", "result_count",
    "old_policy", "source", "tier", "tier_id", "tier_name",
    "inbound_status", "count", "reason_counts",
    "monitor_id", "monitor_kind", "envelope_id", "omitted", "retry", "retry_at",
)
QUERY_PREVIEW_LEN = 40


def text_fields(text: str | None) -> dict[str, str | None]:
    """sha256 + bounded preview for outbound text."""
    if not text:
        return {"text_sha256": None, "text_preview": None}
    return {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_preview": text[:TEXT_PREVIEW_LEN],
    }


def query_fields(query: str | None) -> dict[str, str | None]:
    """sha256 + short preview for a discovery query (find_channels)."""
    if not query:
        return {"query_sha256": None, "query_preview": None}
    return {
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "query_preview": query[:QUERY_PREVIEW_LEN],
    }


_EVENT_NAME_RE = re.compile(r"^[a-z_]{1,40}\*?\Z")
MAX_EVENT_FILTERS = 20


def validate_events(events: list[str] | None) -> list[str] | None:
    """Exact event names, a trailing ``*`` makes a prefix. Raises ValueError when malformed."""
    if not events:
        return None
    if len(events) > MAX_EVENT_FILTERS or not all(_EVENT_NAME_RE.match(e or "") for e in events):
        raise ValueError("invalid events filter")
    return list(events)


def event_matcher(events: list[str] | None) -> Callable[[str], bool] | None:
    if not events:
        return None
    exact = {e for e in events if not e.endswith("*")}
    prefixes = tuple(e[:-1] for e in events if e.endswith("*"))
    return lambda name: name in exact or (bool(prefixes) and name.startswith(prefixes))


@dataclass
class AuditScan:
    """Aggregate of the audit rows in a window: counters only, never row contents."""

    since: str
    rows_read: int = 0
    truncated: bool = False
    # (event, outcome, code) -> rows / sum of ``count`` / newest and oldest ts
    rows: Counter = field(default_factory=Counter)
    count_sum: Counter = field(default_factory=Counter)
    newest: dict[tuple, str] = field(default_factory=dict)
    # event -> merged ``reason_counts``; (event, reason) -> number of ROWS that carry the reason
    reasons: dict[str, Counter] = field(default_factory=dict)
    reason_rows: Counter = field(default_factory=Counter)
    # event -> the newest row's small fields (ts, code, outcome, inbound_status, reason_counts)
    latest: dict[str, dict] = field(default_factory=dict)

    def add(self, rec: dict) -> None:
        event = rec.get("event") or ""
        key = (event, rec.get("outcome"), rec.get("code"))
        self.rows[key] += 1
        count = rec.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            self.count_sum[key] += count
        ts = rec.get("ts") or ""
        if ts > self.newest.get(key, ""):
            self.newest[key] = ts
        reasons = rec.get("reason_counts")
        if isinstance(reasons, dict):
            bucket = self.reasons.setdefault(event, Counter())
            for name, n in reasons.items():
                if isinstance(n, int) and not isinstance(n, bool):
                    bucket[str(name)] += n
                    self.reason_rows[(event, str(name))] += 1
        if event not in self.latest:  # newest first: the first row seen is the newest
            self.latest[event] = {
                "ts": ts, "outcome": rec.get("outcome"), "code": rec.get("code"),
                "inbound_status": rec.get("inbound_status"),
                "reason_counts": dict(reasons) if isinstance(reasons, dict) else None,
            }

    def rows_for(self, event: str, *, outcome: str | None = None, code: str | None = None) -> int:
        return sum(
            n for (e, o, c), n in self.rows.items()
            if e == event and (outcome is None or o == outcome) and (code is None or c == code)
        )

    def sum_for(self, event: str) -> int:
        return sum(n for (e, _o, _c), n in self.count_sum.items() if e == event)

    def newest_for(self, event: str, *, outcome: str | None = None, code: str | None = None) -> str | None:
        stamps = [
            ts for (e, o, c), ts in self.newest.items()
            if e == event and (outcome is None or o == outcome) and (code is None or c == code)
        ]
        return max(stamps) if stamps else None


class RelayAuditLog:
    def __init__(
        self, data_dir: Path, *, max_bytes: int = MAX_BYTES, backups: int = BACKUPS,
        mono: Callable[[], float] = time.monotonic,
    ):
        self._root = Path(data_dir) / "relays"
        self._mono = mono
        self._scan_cache: dict[tuple, tuple[float, AuditScan]] = {}
        self._scan_locks: dict[tuple, asyncio.Lock] = {}
        self._rotations = 0  # bumped under the lock by every rotation (readers detect a shift)
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = threading.Lock()

    def _path(self, relay_id: str) -> Path:
        if not _RELAY_ID_RE.match(relay_id or ""):
            raise ValueError("invalid relay id")
        return self._root / relay_id / AUDIT_FILE

    def _rotate(self, path: Path) -> None:
        self._rotations += 1
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

    def _files(self, relay_id: str) -> list[Path]:
        path = self._path(relay_id)
        return [path] + [path.with_name(f"{AUDIT_FILE}.{i}") for i in range(1, self._backups + 1)]

    def _read_lines(self, f: Path) -> tuple[list[str], int]:
        """One file's lines, newest first, and the rotation count at the read. The lock is held only
        for the read: parsing happens in the caller, outside it, so a writer is never blocked."""
        with self._lock:
            generation = self._rotations
            try:
                text = f.read_text(encoding="utf-8")
            except FileNotFoundError:
                return [], generation
        lines = text.splitlines()
        lines.reverse()
        return lines, generation

    def _iter_records(self, relay_id: str):
        """Parsed records newest first across the rotated files (malformed lines skipped). A rotation
        between two file reads shifts every file one slot down: skip ahead by the number of rotations
        seen so no row is yielded twice."""
        files = self._files(relay_id)
        index, seen = 0, None
        while index < len(files):
            lines, generation = self._read_lines(files[index])
            if seen is not None and generation != seen:
                index += generation - seen  # these rows were already read from their old slot
                seen = generation
                continue
            seen = generation
            index += 1
            for line in lines:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec

    def _query_sync(
        self, relay_id: str, session_id: str | None, channel: str | None, limit: int,
        before: str | None, events: list[str] | None = None, since: str | None = None,
        outcome: str | None = None,
    ) -> list[dict]:
        limit = max(1, min(limit, MAX_QUERY_LIMIT))
        match = event_matcher(events)
        out: list[dict] = []
        for rec in self._iter_records(relay_id):
            ts = rec.get("ts") or ""
            if since and ts < since:
                break  # rows are chronological: everything after this one is older
            if session_id and rec.get("session_id") != session_id:
                continue
            if channel and channel not in (rec.get("channel_id"), rec.get("channel_name")):
                continue
            if before and ts >= before:
                continue
            if match is not None and not match(rec.get("event") or ""):
                continue
            if outcome and rec.get("outcome") != outcome:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    async def query(
        self, relay_id: str, *, session_id: str | None = None, channel: str | None = None,
        limit: int = 100, before: str | None = None, events: list[str] | None = None,
        since: str | None = None, outcome: str | None = None,
    ) -> list[dict]:
        """Newest-first records; ``before`` is an ISO ``ts`` cursor (exclusive), ``since`` an inclusive
        lower bound (early exit), ``events`` exact names or ``prefix*``, ``outcome`` exact."""
        return await asyncio.to_thread(
            self._query_sync, relay_id, session_id, channel, limit, before, events, since, outcome
        )

    # ---- aggregated scan (inbound health, issue #2103 3d-i)

    def _scan_sync(
        self, relay_id: str, since: str, events: list[str] | None, max_rows: int
    ) -> AuditScan:
        match = event_matcher(events)
        scan = AuditScan(since=since)
        for rec in self._iter_records(relay_id):
            ts = rec.get("ts") or ""
            if ts < since:
                break
            if scan.rows_read >= max_rows:
                scan.truncated = True
                break
            scan.rows_read += 1
            event = rec.get("event") or ""
            if match is not None and not match(event):
                continue
            scan.add(rec)
        return scan

    async def scan_since(
        self, relay_id: str, since: str, *, events: list[str] | None = None, max_rows: int = 5000,
        cache_seconds: float = 0.0,
    ) -> AuditScan:
        """Aggregate the rows newer than ``since`` (newest first, stops at the first older row or after
        ``max_rows`` rows READ, then ``truncated`` means "at least"). ``cache_seconds`` > 0 reuses the
        result per (relay, events, max_rows) with single flight, so a poll never scans more often."""
        if cache_seconds <= 0:
            return await asyncio.to_thread(self._scan_sync, relay_id, since, events, max_rows)
        key = (relay_id, tuple(events or ()), max_rows)
        lock = self._scan_locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._scan_cache.get(key)
            if hit is not None and self._mono() - hit[0] < cache_seconds:
                return hit[1]
            scan = await asyncio.to_thread(self._scan_sync, relay_id, since, events, max_rows)
            self._scan_cache[key] = (self._mono(), scan)
            return scan
