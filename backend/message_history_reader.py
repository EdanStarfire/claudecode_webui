"""Message history reader seam (issue #2030, stage 1a).

Window reads over a session's append-only ``messages.jsonl`` sit behind the small
``MessageHistoryReader`` protocol so a database implementation can replace the file
reader later. Routers and the browser only ever see ``HistoryWindow``-shaped JSON and
an *opaque* cursor string (file implementation: ``"b:<byte offset>"``).

The file implementation reads backward from EOF (``read_tail``) or from a cursor
(``read_before``) in fixed-size chunks, so cost is proportional to the bytes of the
window rather than the size of the file. Lines are split on ``\\n`` only — never
``str.splitlines()``, which also breaks on U+2028/U+2029/U+0085 and would fragment a
record written with ``ensure_ascii=False`` (see ``DataStorageManager.read_messages``).
"""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models.messages import LegacyMessageFormatError

logger = logging.getLogger(__name__)

# Backward read granularity. 64 KiB (not 256 KiB): measured on a 112 MiB / 92k-record file, a
# 256 KiB chunk made a full backfill walk read ~4x the file (per-page overshoot).
CHUNK_SIZE = 64 * 1024
_PROBE_SIZE = 4096
_CURSOR_PREFIX = "b:"

# Turn boundary (documented twice, keep in sync with the browser):
#   type == "user" and not metadata.has_tool_results and not a compaction continuation record
# The browser's `turnBoundaryIndices` (MessageList.vue) counts every displayable user
# message; tool-result carrier messages are not user turns and are never displayed as such.
_USER_PREFILTER = b'"user"'
_COMPACTION_CONTINUATION_PREFIX = "This session is being continued from a previous conversation"
_THINKING_TOKENS_PREFILTER = b'"thinking_tokens"'


class CursorInvalidError(Exception):
    """The cursor does not point at the start of a line (file rewritten, or garbage)."""


@dataclass(frozen=True)
class WindowBudget:
    """Bounds for one window. ``min_turns`` user turns are included unless a cap hits first
    (never fewer than the last turn, unless that single turn exceeds ``hard_ceiling_bytes``)."""

    min_turns: int = 2
    max_records: int = 400
    max_bytes: int = 512 * 1024
    hard_ceiling_bytes: int = 2 * 1024 * 1024
    # Tail windows never return fewer than the last turn (caps apply only after one turn
    # is included). ``before`` pages are strictly bounded: over budget with no turn start
    # yet -> cut at a record boundary (``snapped=False``).
    last_turn_guarantee: bool = True


@dataclass
class HistoryWindow:
    records: list[dict[str, Any]]
    start_cursor: str
    has_more_before: bool
    history_id: str
    snapped: bool
    total_bytes: int
    # Instrumentation only (not serialized to the API): bytes actually read from disk.
    bytes_read: int = field(default=0, compare=False)

    def to_payload(self) -> dict[str, Any]:
        return {
            "messages": self.records,
            "start_cursor": self.start_cursor,
            "has_more_before": self.has_more_before,
            "history_id": self.history_id,
            "snapped": self.snapped,
            "total_bytes": self.total_bytes,
        }


class MessageHistoryReader(Protocol):
    async def read_tail(self, budget: WindowBudget) -> HistoryWindow: ...

    async def read_before(self, cursor: str, budget: WindowBudget) -> HistoryWindow: ...

    async def scan_backward(
        self, prefilter: str, max_bytes: int, predicate: Callable[[dict], bool] | None = None
    ) -> dict | None: ...

    def iter_matching(self, prefilters: tuple[str, ...]) -> Iterator[dict]: ...


def encode_cursor(offset: int) -> str:
    return f"{_CURSOR_PREFIX}{offset}"


def decode_cursor(cursor: str) -> int:
    if not isinstance(cursor, str) or not cursor.startswith(_CURSOR_PREFIX):
        raise CursorInvalidError(cursor)
    digits = cursor[len(_CURSOR_PREFIX):]
    if not digits.isascii() or not digits.isdigit():
        raise CursorInvalidError(cursor)
    return int(digits)


def _is_turn_boundary(record: dict) -> bool:
    if record.get("type") != "user":
        return False
    content = record.get("content")
    if isinstance(content, str) and content.startswith(_COMPACTION_CONTINUATION_PREFIX):
        # The compaction continuation record is the last record of a 4-5 record compaction
        # group; a window starting at it would split the group.
        return False
    metadata = record.get("metadata")
    return not (isinstance(metadata, dict) and metadata.get("has_tool_results"))


def _is_thinking_tokens(record: dict) -> bool:
    metadata = record.get("metadata")
    subtype = (metadata.get("subtype") if isinstance(metadata, dict) else None) or record.get(
        "subtype"
    )
    return subtype == "thinking_tokens"


class FileMessageHistoryReader:
    """Reader over a ``messages.jsonl`` file (live session or archive)."""

    def __init__(self, path: Path, label: str = "", *, strict_legacy: bool = True):
        self.path = Path(path)
        self._label = label or self.path.parent.name
        # Live sessions fail loudly on a pre-canonical `_type` record (issue #2125); the
        # archive reader keeps its historical tolerance (it never had that guard).
        self._strict_legacy = strict_legacy

    # ------------------------------------------------------------------ public async API

    async def read_tail(self, budget: WindowBudget) -> HistoryWindow:
        return await asyncio.to_thread(self._read_window, None, budget)

    async def read_before(self, cursor: str, budget: WindowBudget) -> HistoryWindow:
        offset = decode_cursor(cursor)
        return await asyncio.to_thread(self._read_window, offset, budget)

    async def scan_backward(
        self, prefilter: str, max_bytes: int, predicate: Callable[[dict], bool] | None = None
    ) -> dict | None:
        return await asyncio.to_thread(self._scan_backward, prefilter.encode(), max_bytes, predicate)

    def iter_matching(self, prefilters: tuple[str, ...]) -> Iterator[dict]:
        """Forward stream of parsed records whose line contains any prefilter substring.

        Synchronous: callers run the whole consumption in a worker thread
        (``asyncio.to_thread``) so no whole-file parse happens on the event loop.
        Non-matching lines are never JSON-decoded.
        """
        needles = tuple(p.encode() for p in prefilters)
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            return
        with f:
            carry = b""
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                data = carry + chunk
                lines = data.split(b"\n")
                carry = lines.pop()
                for line in lines:
                    yield from self._match_line(line, needles)
            if carry:
                yield from self._match_line(carry, needles)

    # ------------------------------------------------------------------ internals

    def _match_line(self, line: bytes, needles: tuple[bytes, ...]) -> Iterator[dict]:
        if not line.strip() or not any(n in line for n in needles):
            return
        record = self._parse(line)
        if record is not None:
            yield record

    def _parse(self, line: bytes) -> dict | None:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Failed to parse message line: %r... Error: %s", line[:100], e)
            return None
        if not isinstance(record, dict):
            return None
        stored_type = record.get("_type")
        if stored_type and self._strict_legacy:
            raise LegacyMessageFormatError(self._label, stored_type)
        return record

    def _history_id(self, st: os.stat_result) -> str:
        first_id = ""
        try:
            with open(self.path, "rb") as f:
                head = f.read(64 * 1024)
                while b"\n" not in head and len(head) < st.st_size:
                    more = f.read(256 * 1024)
                    if not more:
                        break
                    head += more
            first_line = head.split(b"\n", 1)[0].strip()
            if first_line:
                try:
                    first_id = str(json.loads(first_line).get("message_id") or "")
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    first_id = hashlib.sha1(first_line).hexdigest()
        except OSError:
            pass
        return hashlib.sha1(f"{st.st_ino}:{first_id}".encode()).hexdigest()[:16]

    def _complete_end(self, f, size: int) -> tuple[int, int]:
        """Return (end, bytes_read): the offset just past the last COMPLETE line.

        A trailing segment with no ``\\n`` is a partial write in progress and is excluded,
        unless it is already a complete JSON object (legacy files without a final newline).
        """
        if size == 0:
            return 0, 0
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return size, 1
        read = 1
        pos = size
        tail = b""
        while pos > 0:
            start = max(0, pos - CHUNK_SIZE)
            f.seek(start)
            chunk = f.read(pos - start)
            read += len(chunk)
            tail = chunk + tail
            pos = start
            nl = tail.rfind(b"\n")
            if nl != -1:
                segment_start = pos + nl + 1
                segment = tail[nl + 1:]
                break
        else:
            segment_start = 0
            segment = tail
        try:
            json.loads(segment)
            return size, read
        except (json.JSONDecodeError, UnicodeDecodeError):
            return segment_start, read

    def _lines_backward(self, f, end: int, stats: list[int]) -> Iterator[tuple[int, bytes]]:
        """Yield (line_start_offset, line_bytes) newest-first for complete lines before ``end``.

        ``stats[0]`` accumulates bytes read. Empty lines are yielded too (callers skip them)
        so that offsets stay exact.
        """
        pos = end
        buf = b""
        while pos > 0:
            start = max(0, pos - CHUNK_SIZE)
            f.seek(start)
            chunk = f.read(pos - start)
            stats[0] += len(chunk)
            buf = chunk + buf
            pos = start
            parts = buf.split(b"\n")
            # parts[0] may be the tail of a line starting before `pos`, unless pos == 0.
            first = parts[0]
            rest = parts[1:]
            if pos == 0:
                rest = parts
                first = b""
            offset = pos + len(first) + (1 if pos != 0 else 0)
            # offsets for `rest` lines, computed forward then yielded in reverse
            starts = []
            cur = offset
            for line in rest:
                starts.append(cur)
                cur += len(line) + 1
            for line_start, line in zip(reversed(starts), reversed(rest), strict=True):
                # (the empty element after a trailing '\n' is yielded too; callers skip blanks)
                yield line_start, line
            buf = first

    def _read_window(self, before_offset: int | None, budget: WindowBudget) -> HistoryWindow:
        stats = [0]
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            return HistoryWindow([], encode_cursor(0), False, self._empty_history_id(), True, 0)
        with f:
            st = os.fstat(f.fileno())
            size = st.st_size
            end, read = self._complete_end(f, size)
            total_end = end
            stats[0] += read
            history_id = self._history_id(st)

            if before_offset is not None:
                if before_offset > end:
                    raise CursorInvalidError(encode_cursor(before_offset))
                if before_offset > 0:
                    f.seek(before_offset - 1)
                    stats[0] += 1
                    if f.read(1) != b"\n":
                        raise CursorInvalidError(encode_cursor(before_offset))
                end = before_offset

            selected: list[tuple[int, bytes]] = []  # newest-first
            bytes_used = 0
            counted = 0
            turns = 0
            boundary_idx = -1
            snapped = True
            reached_start = True
            ceiling_hit = False

            for line_start, line in self._lines_backward(f, end, stats):
                if not line.strip():
                    continue
                cost = len(line) + 1
                uncounted = False
                if _THINKING_TOKENS_PREFILTER in line:
                    rec = self._try_json(line)
                    uncounted = rec is not None and _is_thinking_tokens(rec)
                if not uncounted:
                    over = counted >= budget.max_records or bytes_used + cost > budget.max_bytes
                    if turns >= 1 and over:
                        reached_start = False
                        break
                    if turns == 0 and selected and (
                        bytes_used + cost > budget.hard_ceiling_bytes
                        or (over and not budget.last_turn_guarantee)
                    ):
                        reached_start = False
                        ceiling_hit = True
                        break
                    counted += 1
                    bytes_used += cost
                selected.append((line_start, line))
                if _USER_PREFILTER in line:
                    rec = self._try_json(line)
                    if rec is not None and _is_turn_boundary(rec):
                        turns += 1
                        boundary_idx = len(selected) - 1
                        if turns >= budget.min_turns:
                            reached_start = False
                            break

            if ceiling_hit:
                snapped = False
            elif not reached_start and boundary_idx >= 0:
                selected = selected[: boundary_idx + 1]

            start_offset = selected[-1][0] if selected else end
            has_more_before = start_offset > 0 and self._has_content_before(f, start_offset, stats)

            records: list[dict] = []
            for _, line in reversed(selected):
                rec = self._parse(line)
                if rec is not None:
                    records.append(rec)

            return HistoryWindow(
                records=records,
                start_cursor=encode_cursor(start_offset),
                has_more_before=has_more_before,
                history_id=history_id,
                snapped=snapped,
                total_bytes=total_end,
                bytes_read=stats[0],
            )

    def _has_content_before(self, f, offset: int, stats: list[int]) -> bool:
        """True if any non-whitespace byte precedes ``offset`` (blank-line-only prefixes
        must not advertise more history). Reads small probes backward and stops at the
        first non-blank byte, so the common case costs one probe."""
        pos = offset
        while pos > 0:
            start = max(0, pos - _PROBE_SIZE)
            f.seek(start)
            chunk = f.read(pos - start)
            stats[0] += len(chunk)
            if chunk.strip():
                return True
            pos = start
        return False

    def _empty_history_id(self) -> str:
        return hashlib.sha1(b"missing:").hexdigest()[:16]

    @staticmethod
    def _try_json(line: bytes) -> dict | None:
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return rec if isinstance(rec, dict) else None

    def _scan_backward(
        self, needle: bytes, max_bytes: int, predicate: Callable[[dict], bool] | None
    ) -> dict | None:
        stats = [0]
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            return None
        with f:
            size = os.fstat(f.fileno()).st_size
            end, _ = self._complete_end(f, size)
            for _, line in self._lines_backward(f, end, stats):
                if needle in line:
                    rec = self._try_json(line)
                    if rec is not None and (predicate is None or predicate(rec)):
                        return rec
                if stats[0] >= max_bytes:
                    return None
        return None


# ---------------------------------------------------------------------- shared helpers

LATEST_INIT_SCAN_BYTES = 8 * 1024 * 1024


def _is_init_record(record: dict) -> bool:
    if record.get("type") != "system":
        return False
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return (
        (metadata.get("subtype") == "init" or record.get("subtype") == "init")
        and bool(metadata.get("init_data"))
    )


async def find_latest_init(reader: MessageHistoryReader) -> dict | None:
    """init_data of the newest ``system/init`` record (bounded backward scan; None if absent)."""
    record = await reader.scan_backward('"init"', LATEST_INIT_SCAN_BYTES, _is_init_record)
    return record["metadata"]["init_data"] if record else None


async def read_history_payload(
    reader: MessageHistoryReader, *, before: str | None, budget: WindowBudget
) -> dict[str, Any]:
    """API payload for ``window=tail`` (``before`` None, includes ``latest_init``) or a
    ``before=<cursor>`` page. Raises ``CursorInvalidError`` for an unusable cursor."""
    if before is None:
        window = await reader.read_tail(budget)
        payload = window.to_payload()
        payload["latest_init"] = await find_latest_init(reader)
    else:
        window = await reader.read_before(before, budget)
        payload = window.to_payload()
    return payload


MAX_RECORDS_CAP = 5000
MAX_BYTES_CAP = 4 * 1024 * 1024
_UNBOUNDED_TURNS = 10**9


def build_budget(
    *,
    before: bool,
    min_turns: int | None = None,
    max_records: int | None = None,
    max_bytes: int | None = None,
) -> WindowBudget:
    """Budget for a request. Tail windows default to the provisional 2 turns / 400 records /
    512 KiB; ``before`` pages default to byte/record-bounded (no turn target), 256 KiB."""
    defaults = WindowBudget()
    return WindowBudget(
        min_turns=(
            max(1, min_turns) if min_turns is not None
            else (_UNBOUNDED_TURNS if before else defaults.min_turns)
        ),
        max_records=min(max(1, max_records), MAX_RECORDS_CAP) if max_records else defaults.max_records,
        max_bytes=(
            min(max(1024, max_bytes), MAX_BYTES_CAP) if max_bytes
            else (256 * 1024 if before else defaults.max_bytes)
        ),
        hard_ceiling_bytes=defaults.hard_ceiling_bytes,
        last_turn_guarantee=not before,
    )
