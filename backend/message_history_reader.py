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
import functools
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import history_search as hs
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


@functools.lru_cache(maxsize=32)
def _needle_pattern(needles: tuple[bytes, ...], regex: bool = False) -> "re.Pattern[bytes]":
    return re.compile(b"|".join(n if regex else re.escape(n) for n in needles))


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

    def iter_matching(
        self, prefilters: tuple[str, ...], *, cancel: Any = None
    ) -> Iterator[dict]: ...

    def iter_records_with_offsets(
        self, prefilters: tuple[str, ...], *, cancel: Any = None,
        progress: list[int] | None = None, regex: bool = False,
        skip_line: Callable[[bytes, int, int], bool] | None = None,
        include_unterminated: bool = True, throttle: float = 0.0,
    ) -> Iterator[tuple[int, int, dict]]: ...

    def search(
        self, query: str, cursor: str | None, caps: "hs.SearchCaps", cancel: Any = None
    ) -> "hs.SearchPage": ...

    def probe(self) -> tuple[str, int]: ...

    def read_range(self, start: int, end: int) -> Iterator[tuple[int, int, dict]]: ...


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

    def iter_matching(
        self, prefilters: tuple[str, ...], *, cancel: Any = None
    ) -> Iterator[dict]:
        """Forward stream of parsed records whose line contains any prefilter substring.

        Synchronous: callers run the whole consumption in a worker thread
        (``asyncio.to_thread`` or the cold-scan pool) so no whole-file parse happens on the
        event loop. Non-matching lines are never JSON-decoded. ``cancel`` (anything with
        ``is_set()``) is checked between read batches; a set flag ends the stream early.
        """
        for _, _, record in self.iter_records_with_offsets(prefilters, cancel=cancel):
            yield record

    def iter_records_with_offsets(
        self, prefilters: tuple[str, ...], *, cancel: Any = None,
        progress: list[int] | None = None, regex: bool = False,
        skip_line: Callable[[bytes, int, int], bool] | None = None,
        include_unterminated: bool = True, throttle: float = 0.0,
    ) -> Iterator[tuple[int, int, dict]]:
        """Like :meth:`iter_matching` but yields ``(start_offset, end_offset, record)``.

        ``end_offset`` is just past the record's line (including its ``\\n`` when present).
        ``progress[0]`` (when given) is kept at the offset just past the last line the scan has
        fully consumed, matching or not, so a caller knows how far the file was covered even if
        the tail had no matching line. A trailing segment without ``\\n`` counts only when it
        is a complete JSON record (a partial write in progress is left for the live feed);
        ``include_unterminated=False`` leaves such a segment to the live feed entirely (the
        index scan: a writer may still be finishing that record).
        ``skip_line(data, line_start, line_end)`` may veto parsing of a matching line (a skipped
        line neither yields nor advances ``progress`` on its own). ``regex=True`` treats the
        prefilters as regex fragments. Between 1 MiB batches the scan yields the GIL; with
        ``throttle`` > 0 it sleeps ``throttle`` x the batch's busy time (duty cycle
        1 / (1 + throttle)), so a long scan leaves the event loop thread GIL headroom.
        """
        needles = tuple(p.encode() for p in prefilters)
        pattern = _needle_pattern(needles, regex)
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            return
        with f:
            carry = b""
            consumed = 0  # file offset of the first byte of `carry`
            while True:
                if cancel is not None and cancel.is_set():
                    return
                batch_start = time.perf_counter()
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                data = carry + chunk
                last_nl = data.rfind(b"\n")
                if last_nl == -1:
                    carry = data
                    continue
                complete = last_nl + 1  # bytes [0, complete) are whole lines
                pos = 0
                # One C-speed regex search per MATCHING line; non-matching lines (the bulk:
                # tool results, assistant text) are never visited from Python at all.
                while True:
                    m = pattern.search(data, pos, complete)
                    if m is None:
                        break
                    line_start = data.rfind(b"\n", 0, m.start()) + 1
                    line_end = data.find(b"\n", m.start(), complete)
                    if skip_line is not None and skip_line(data, line_start, line_end):
                        pos = line_end + 1
                        continue
                    record = self._parse(data[line_start:line_end])
                    if record is not None:
                        yield consumed + line_start, consumed + line_end + 1, record
                    pos = line_end + 1
                carry = data[complete:]
                consumed += complete
                if progress is not None:
                    progress[0] = consumed
                time.sleep((time.perf_counter() - batch_start) * throttle if throttle else 0)
            if carry and include_unterminated:
                end = consumed + len(carry)
                if carry.strip():
                    if pattern.search(carry):
                        record = self._try_json(carry)
                        if record is not None:
                            stored_type = record.get("_type")
                            if stored_type and self._strict_legacy:
                                raise LegacyMessageFormatError(self._label, stored_type)
                            yield consumed, end, record
                            if progress is not None:
                                progress[0] = end
                    elif self._try_json(carry) is not None and progress is not None:
                        progress[0] = end

    def read_range(self, start: int, end: int) -> Iterator[tuple[int, int, dict]]:
        """Parsed records of the whole lines in ``[start, end)`` as ``(start_offset, end_offset,
        record)``, read lazily in 1 MiB blocks so a caller can stop early (used to resolve the gap
        between two prefilter matches only while a compaction-group prefix is pending)."""
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            return
        with f:
            f.seek(start)
            offset = start
            carry = b""
            while offset + len(carry) < end:
                chunk = f.read(min(1024 * 1024, end - offset - len(carry)))
                if not chunk:
                    return
                data = carry + chunk
                lines = data.split(b"\n")
                carry = lines.pop()
                for line in lines:
                    line_end = offset + len(line) + 1
                    if line.strip():
                        record = self._parse(line)
                        if record is not None:
                            yield offset, line_end, record
                    offset = line_end
            if carry.strip():
                record = self._try_json(carry)
                if record is not None:
                    yield offset, offset + len(carry), record

    def search(
        self, query: str, cursor: str | None, caps: "hs.SearchCaps", cancel: Any = None
    ) -> "hs.SearchPage":
        """One newest-first page of the whole-history substring search (issue #2030 stage 3).

        Synchronous worker-thread code (the search pool). ``cursor`` is None for the first page or
        a previous page's ``next_cursor``. Raises ``hs.SearchQueryError`` for a bad query and
        ``CursorInvalidError`` for a cursor that is malformed, from another file generation, or
        not at a line start. See ``history_search.py`` for the matching rules.
        """
        return _FileSearch(self, hs.QueryMatcher(query), cursor, caps, cancel).run()

    # ------------------------------------------------------------------ internals

    def probe(self) -> tuple[str, int]:
        """``(history_id, size)`` of the file as it is now (cheap: one stat + head read)."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return self._empty_history_id(), 0
        return self._history_id(st), st.st_size

    # ------------------------------------------------------------------ internals

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


# ---------------------------------------------------------------------- whole-history search

_SEARCH_BLOCK = 1024 * 1024
_PACE_AFTER_S = 0.003
_PACE_THROTTLE = 3.0


class _FileSearch:
    """One backward search page over a ``messages.jsonl`` (see ``FileMessageHistoryReader.search``).

    The file is read backward in ~1 MiB blocks cut at line starts. Per block, candidate lines come
    from the matcher's prefilter (ASCII query: lower-case the block once and ``bytes.find``;
    non-matching lines are never visited from Python; other queries: see ``QueryMatcher``). Lines are handled strictly newest-first. A ``tool_call`` candidate yields hits that
    wait in ``pending`` (keyed by ``tool_use_id``, newest record wins) until the scan reaches the
    message record that lists the tool id in ``metadata.tool_uses``; that message's matches
    (content, thinking, then its tools in ``tool_uses`` order) are emitted atomically. Pending ids
    are also located by a direct byte search so a parent that is not itself a query candidate is
    still visited in order. A page ends at a message boundary once a cap is reached; hits still
    waiting for their parent ride in the continuation cursor.
    """

    def __init__(self, reader, matcher, cursor, caps, cancel):
        self.reader = reader
        self.m = matcher
        self.cursor = cursor
        self.caps = caps
        self.cancel = cancel
        self.results: list[dict] = []
        self.pending: dict[str, list[dict]] = {}
        self.pending_bytes: dict[str, bytes] = {}
        self.seen_tools: set[str] = set()
        self.scanned = 0
        self.progressed = False
        self.dropped = 0
        self._busy_since = time.perf_counter()
        self.started = time.monotonic()

    def _pace(self) -> None:
        """Duty-cycle throttle: after ``_PACE_AFTER_S`` of continuous work sleep a fraction of it,
        so the GIL is regularly free for the event loop and the critical-path reads (the index
        scan does the same, see ``iter_records_with_offsets(throttle=...)``)."""
        now = time.perf_counter()
        busy = now - self._busy_since
        if busy >= _PACE_AFTER_S:
            time.sleep(busy * _PACE_THROTTLE)
            self._busy_since = time.perf_counter()

    # -- caps ----------------------------------------------------------------

    def _stop_reason(self, hard: bool) -> str | None:
        scale = self.caps.grace if hard else 1.0
        if len(self.results) >= self.caps.max_results and not hard:
            return "results"
        if self.scanned >= self.caps.max_scan_bytes * scale:
            return "bytes"
        if time.monotonic() - self.started >= self.caps.max_wall_s * scale:
            return "time"
        return None

    def _may_stop(self) -> str | None:
        """Stop reason when the page may end here: a cap is reached and the carry is small, or the
        hard grace limit is reached (then the oldest surplus carried hits are dropped)."""
        reason = self._stop_reason(hard=False)
        if reason is None:
            return None
        if self._carry_too_big():
            if self._stop_reason(hard=True) is None:
                return None  # keep scanning: the parents of the waiting hits are probably close
            # Newest tool records come first and have the nearest parents: drop the latest-added.
            for tool_id in reversed(list(self.pending)):
                if not self._carry_too_big():
                    break
                self._drop_pending(tool_id)
        return reason

    def _carry_too_big(self) -> bool:
        """The continuation cursor carries the waiting hits: bound its size (tools and hits)."""
        return len(self.pending) > hs.MAX_CARRY_TOOLS or (
            sum(len(h) for h in self.pending.values()) > hs.MAX_CARRY_HITS
        )

    def _drop_pending(self, tool_id: str) -> None:
        self.dropped += len(self.pending.pop(tool_id, ()))
        self.pending_bytes.pop(tool_id, None)

    # -- driver ----------------------------------------------------------------

    def run(self) -> "hs.SearchPage":
        try:
            f = open(self.reader.path, "rb")
        except FileNotFoundError:
            return hs.SearchPage(
                [], None, True, self.reader._empty_history_id(), 0, 1.0, 0
            )
        with f:
            st = os.fstat(f.fileno())
            end, read = self.reader._complete_end(f, st.st_size)
            self.scanned += read
            history_id = self.reader._history_id(st)
            pos = end
            if self.cursor is not None:
                parsed = hs.decode_search_cursor(self.cursor)
                if parsed is None or parsed.history_id != history_id or parsed.offset > end:
                    raise CursorInvalidError(self.cursor)
                if parsed.offset > 0:
                    f.seek(parsed.offset - 1)
                    if f.read(1) != b"\n":
                        raise CursorInvalidError(self.cursor)
                pos = parsed.offset
                for tool_id, hits in parsed.pending.items():
                    self._add_pending(tool_id, hits)
            next_off, stopped_by, cancelled = self._scan(f, pos)
            exhausted = next_off == 0 and stopped_by is None and not cancelled
            pending = {} if exhausted else dict(self.pending)
            next_cursor = (
                None
                if exhausted or cancelled
                else hs.encode_search_cursor(history_id, next_off, pending)
            )
            # (an exhausted scan leaves orphan tool calls unmatched, like the browser)
            return hs.SearchPage(
                self.results,
                next_cursor,
                exhausted,
                history_id,
                self.scanned,
                (end - next_off) / end if end else 1.0,
                end,
                dropped_tool_hits=self.dropped,
                stopped_by=stopped_by,
                cancelled=cancelled,
            )

    def _add_pending(self, tool_id: str, hits: list[dict]) -> None:
        raw = tool_id.encode()
        if not tool_id.isascii() or b'"' in raw or b"\\" in raw or len(self.pending) >= hs.MAX_PENDING_TOOLS:
            self.dropped += len(hits)
            return
        self.pending[tool_id] = hits
        self.pending_bytes[tool_id] = raw

    def _scan(self, f, pos: int) -> tuple[int, str | None, bool]:
        """Scan backward from ``pos``; returns (resume offset, stop reason, cancelled)."""
        block = _SEARCH_BLOCK
        while pos > 0:
            if self.cancel is not None and self.cancel.is_set():
                return pos, None, True
            # (always scan one block per page first: a page must make progress)
            reason = self._may_stop() if self.progressed else None
            if reason is not None:
                return pos, reason, False
            start = max(0, pos - block)
            f.seek(start)
            chunk = f.read(pos - start)
            self.scanned += len(chunk)
            if start > 0:
                first_nl = chunk.find(b"\n")
                if first_nl == -1 or start + first_nl + 1 >= pos:
                    block *= 2  # a single line longer than the block: widen and retry
                    continue
                usable_start = start + first_nl + 1
                region = chunk[first_nl + 1 :]
            else:
                usable_start = 0
                region = chunk
            block = _SEARCH_BLOCK
            self.progressed = True
            stop_at = self._scan_region(region, usable_start)
            if stop_at is not None:
                # (no stop reason = the cancel flag fired mid-region: the page is incomplete)
                return stop_at[0], stop_at[1], stop_at[1] is None
            if self.cancel is not None and self.cancel.is_set():
                return usable_start, None, True
            pos = usable_start
        return 0, None, False

    # -- one region ----------------------------------------------------------------

    def _candidates(self, region: bytes) -> list[tuple[int, int]]:
        """Candidate (line_start, line_end) pairs of ``region`` in ascending order."""
        out: list[tuple[int, int]] = []
        m = self.m
        if m.mode == "bytes" and not any(alias in region for alias in hs.ALIAS_BYTES):
            # ASCII query on a block without KELVIN SIGN / U+0130: lower-case once, then `find`
            # (5-10x faster than the case-insensitive class regex, and shorter GIL holds).
            lowered, needle, n = region.lower(), m.needle, len(region)
            find = lowered.find
            pos = 0
            while True:
                hit = find(needle, pos)
                if hit == -1:
                    break
                ls = region.rfind(b"\n", 0, hit) + 1
                le = region.find(b"\n", hit)
                if le == -1:
                    le = n
                out.append((ls, le))
                pos = le + 1
            return out
        if m.mode == "bytes":
            search = m.pattern.search
            pos = 0
            n = len(region)
            while True:
                hit = search(region, pos)
                if hit is None:
                    break
                ls = region.rfind(b"\n", 0, hit.start()) + 1
                le = region.find(b"\n", hit.start())
                if le == -1:
                    le = n
                out.append((ls, le))
                pos = le + 1
            return out
        pos = 0
        n = len(region)
        while pos < n:
            le = region.find(b"\n", pos)
            if le == -1:
                le = n
            if le > pos and (m.mode == "parse" or m.line_candidate(region[pos:le])):
                out.append((pos, le))
            pos = le + 1
            self._pace()
            if self.cancel is not None and self.cancel.is_set():
                break
        return out

    def _scan_region(self, region: bytes, base: int):
        cands = self._candidates(region)
        self._pace()
        qi = len(cands) - 1
        limit = len(region)
        nxt: dict[str, int] = {}
        processed_counter = 0
        while True:
            if self.cancel is not None and self.cancel.is_set():
                return base, None
            best = -1
            if qi >= 0:
                best = cands[qi][0]
            for tool_id, raw in self.pending_bytes.items():
                p = nxt.get(tool_id)
                if p is None or p >= limit:
                    p = region.rfind(raw, 0, limit)
                    nxt[tool_id] = p
                if p > best:
                    best = p
            if best < 0:
                break
            ls = region.rfind(b"\n", 0, best) + 1
            while qi >= 0 and cands[qi][0] >= ls:
                qi -= 1
            le = region.find(b"\n", ls)
            if le == -1:
                le = len(region)
            limit = ls
            self._handle_line(region[ls:le], base + ls)
            self._pace()
            processed_counter += 1
            if processed_counter % 8 == 0 or self.results:
                reason = self._may_stop()
                if reason is not None:
                    return base + ls, reason
        return None

    # -- one line ----------------------------------------------------------------

    def _handle_line(self, line: bytes, offset: int) -> None:
        if not line or line.isspace():
            return
        record = self.reader._try_json(line)
        if record is None:
            return
        if record.get("type") == "tool_call":
            tool_id = record.get("tool_use_id")
            if not isinstance(tool_id, str) or not tool_id or tool_id in self.seen_tools:
                return
            self.seen_tools.add(tool_id)
            hits = hs.tool_call_hits(self.m, record)
            for hit in hits:
                hit["offset"] = offset
            if hits and tool_id not in self.pending:
                self._add_pending(tool_id, hits)
            return
        hits = hs.message_hits(self.m, record)
        for hit in hits:
            hit["offset"] = offset
        resolved = []
        for tool_id in hs.tool_use_ids(record):
            if tool_id in self.pending:
                if len(hits) <= hs.MAX_HITS_PER_MESSAGE:
                    hits = hits + self.pending[tool_id]
                else:
                    self.dropped += len(self.pending[tool_id])
                resolved.append(tool_id)
        for tool_id in set(resolved):
            self.pending.pop(tool_id, None)
            self.pending_bytes.pop(tool_id, None)
        if hits:
            self.results.extend(
                hs.finalize_group(
                    record,
                    hits,
                    record.get("id") or record.get("message_id") or None,
                )
            )
