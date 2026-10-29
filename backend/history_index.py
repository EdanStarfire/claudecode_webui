"""Server history index reducer (issue #2030, stage 2 PR A).

A pure reducer over a session's stored records, in file order with each record's start byte
offset, producing what the browser cannot derive from a partially loaded history:

- ``turns``: the *display-user* records — the rows the browser's turn navigation counts
  (``MessageList.vue`` ``turnBoundaryIndices`` over ``buildDisplayableItems``): a ``user``
  record that survives ``shouldDisplayMessage`` (``frontend/src/utils/displayableItems.js``)
  and is not part of a compaction group.
- ``compactions``: the first-record ``message_id`` of every compaction group (4- or 5-record
  pattern recognised exactly like the browser's ``isCompactionStart``).

Like ``task_list.py`` there are two implementations (this one, the browser's) that must not
drift: the shared case file ``backend/tests/fixtures/history_index_cases.json`` is run by both
a pytest and a vitest test, and a fixture-level test compares the two on every committed fixture.

Sequence elements mirror the browser's message list: every record except ``tool_call`` (tool
store) and ``status/requesting`` is an element, because ``isCompactionStart`` needs the group's
records to be consecutive list entries. Records other than user/status/init/boundary only matter
while a group prefix is pending, so a file scan visits just the prefiltered lines
(``INDEX_PREFILTERS``) and reads the (rare) gap between matches only while a prefix is pending
(``iter_gap_records``); the live feed sees every record.
"""

import re
from array import array
from typing import Any

from .message_history_reader import _COMPACTION_CONTINUATION_PREFIX
from .task_list import _truthy as _js_truthy

# Substring prefilters: every sequence element's serialized line contains one of these, so
# non-matching lines (tool_call/result/assistant/...) are never JSON-decoded by the scan.
# Regex fragments (``regex=True``): `"user"` for user records, and the subtype of the system
# records that can belong to a group. NOT a bare `"status"` — every tool_call record carries
# `"status": "completed"`, which would make the scan parse most of the file.
INDEX_PREFILTERS = ('"user"', r'"subtype"\s*:\s*"(?:status|init|compact_boundary)"')

# JS `\s` (no `u` flag) — what ``String.prototype.trim`` strips. Python's ``str.strip()`` differs
# (it also strips U+001C-U+001F and does not strip U+FEFF).
_JS_WS_CHARS = (
    "\t\n\u000b\u000c\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
)
_TOOL_RESULTS_RE = re.compile(r"Tool results?: [0-9]+ results?", re.ASCII | re.IGNORECASE)

# Element kinds.
_USER = "user"
_CONTINUATION = "continuation"
_COMPACTING = "compacting"
_STATUS_NULL = "status_null"
_INIT = "init"
_BOUNDARY = "boundary"
_OTHER = "other"  # in the browser list (breaks a pattern) but neither a turn nor a group member

_PATTERN_4 = (_COMPACTING, _STATUS_NULL, _BOUNDARY, _CONTINUATION)
_PATTERN_5 = (_COMPACTING, _STATUS_NULL, _INIT, _BOUNDARY, _CONTINUATION)


def resolve_message_id(record: dict) -> str | None:
    """``resolveMessageId`` (messageSearch.js): ``message.id || message.message_id``."""
    return record.get("id") or record.get("message_id") or None


def _metadata(record: dict) -> dict:
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _content_text(record: dict) -> str:
    """``message.content || ''`` for the string ops of ``shouldDisplayMessage``."""
    content = record.get("content")
    return content if isinstance(content, str) else ""


def is_display_user(record: dict) -> bool:
    """The user-record part of the browser's ``shouldDisplayMessage`` (record is ``type=user``)."""
    metadata = _metadata(record)
    content = _content_text(record)
    stripped = content.strip(_JS_WS_CHARS)
    if stripped == "Tool loaded.":  # issue #684
        return False
    if _js_truthy(metadata.get("has_tool_results")):
        if _TOOL_RESULTS_RE.fullmatch(content) or stripped == "":
            return False
    if "<command-message>" in content and "skill is running" in content:
        return False
    if content.startswith("Base directory for this skill:"):
        return False
    if content.startswith("(Re-invocation of /") and "previously loaded" in content:  # #1724
        return False
    if "<command-message>" in content and "<command-name>" in content and "<command-args>" in content:
        return False
    if "ARGUMENTS:" in content and ("<command-name>" in content or "\nARGUMENTS:" in content):
        return False
    if _js_truthy(metadata.get("parent_tool_use_id")) and not _js_truthy(
        metadata.get("has_tool_results")
    ):
        return False
    return True


def _classify(record: dict, archive: bool) -> str | None:
    """Sequence-element kind of a record, or None when the record never reaches the browser's
    message list (``tool_call`` goes to the tool store; ``status/requesting`` is dropped by
    ``_applyMessageRecord``; archive-only filters). EVERY other record is an element — the
    browser's ``isCompactionStart`` matches consecutive LIST entries, so an assistant, hook,
    permission or other record interleaved inside a compaction group breaks the pattern."""
    rtype = record.get("type")
    if not rtype or rtype == "tool_call":
        return None
    if rtype == "user":
        content = record.get("content")
        if archive and isinstance(content, list) and not any(
            not (isinstance(b, dict) and b.get("type") == "tool_result") for b in content
        ):
            # archive-only filter (message.js _applyArchiveRecords): tool-result-only user rows
            return None
        if isinstance(content, str) and content.startswith(_COMPACTION_CONTINUATION_PREFIX):
            return _CONTINUATION
        return _USER
    if rtype != "system":
        return _OTHER
    metadata = _metadata(record)
    subtype = record.get("subtype") or metadata.get("subtype")
    if subtype == "status":
        init_data = metadata.get("init_data")
        status = init_data.get("status") if isinstance(init_data, dict) else None
        if status == "requesting":  # dropped by _applyMessageRecord (issues #1486/#1575)
            return None
        if metadata.get("subtype") != "status":
            return _OTHER
        if status == "compacting":
            return _COMPACTING
        return _STATUS_NULL if status is None else _OTHER
    msubtype = metadata.get("subtype")
    if msubtype == "init":
        if archive and not record.get("content"):  # archive drops empty-content init rows
            return None
        return _INIT
    if msubtype == "compact_boundary":
        return _BOUNDARY
    return _OTHER


class HistoryIndexBuildingError(Exception):
    """No index is available within the waiter cap (route answers 503 ``index_building``)."""


class HistoryIndexState:
    """Streaming reducer state: feed records in file order via :meth:`apply`.

    ``end`` is the byte offset just past the last line consumed (the scan/feed progress marker
    used for hydrate/feed de-duplication and gap detection).
    """

    def __init__(self, history_id: str = "", *, archive: bool = False):
        self.history_id = history_id
        self.archive = archive
        self.end = 0
        # Compact storage: ~8 B offset + one id string per turn.
        self._turn_offsets = array("Q")
        self._turn_ids: list[str] = []
        self.compactions: list[str] = []
        self._buf: list[tuple[str, int, dict]] = []  # pending compaction-group prefix

    # ------------------------------------------------------------------ reducer

    def advance(self, end: int) -> None:
        if end > self.end:
            self.end = end

    def apply(self, offset: int, end: int, record: dict) -> None:
        """Consume one record that starts at byte ``offset`` and whose line ends at ``end``."""
        kind = _classify(record, self.archive)
        if kind is not None and (kind != _OTHER or self._buf):
            # An unrelated record only matters while a group prefix is pending (it breaks it).
            self._push((kind, offset, record))
        self.advance(end)

    @property
    def group_pending(self) -> bool:
        """A compaction-group prefix is buffered: the next record (even an unrelated one) matters."""
        return bool(self._buf)

    def _push(self, element: tuple[str, int, dict]) -> None:
        buf = self._buf
        buf.append(element)
        while buf:
            kinds = tuple(k for k, _, _ in buf)
            if kinds == _PATTERN_4 or kinds == _PATTERN_5:
                key = resolve_message_id(buf[0][2])
                if key:
                    self.compactions.append(key)
                buf.clear()
                return
            if kinds == _PATTERN_4[: len(kinds)] or kinds == _PATTERN_5[: len(kinds)]:
                return
            self._plain(buf.pop(0))

    def _plain(self, element: tuple[str, int, dict]) -> None:
        kind, offset, record = element
        if kind not in (_USER, _CONTINUATION) or not is_display_user(record):
            return
        message_id = resolve_message_id(record)
        if message_id:
            self._turn_offsets.append(offset)
            self._turn_ids.append(message_id)

    # ------------------------------------------------------------------ output

    @property
    def turn_count(self) -> int:
        return len(self._turn_ids)

    def turn_ids(self) -> list[str]:
        return list(self._turn_ids)

    def turns(self) -> list[list[Any]]:
        """``[[cursor, message_id, size_hint], ...]`` — ``size_hint`` is the record's byte
        position (a hint, like the history progress use of ``b:`` cursors)."""
        return [
            [f"b:{offset}", message_id, offset]
            for offset, message_id in zip(self._turn_offsets, self._turn_ids, strict=True)
        ]

    def to_payload(self) -> dict[str, Any]:
        return {
            "history_id": self.history_id,
            "total_bytes": self.end,
            "turns": self.turns(),
            "compactions": list(self.compactions),
        }

    def approx_bytes(self) -> int:
        """Rough retained size, for the cache-memory report (ids dominate)."""
        return (
            self._turn_offsets.buffer_info()[1] * 8
            + sum(len(i) + 49 for i in self._turn_ids)
            + 8 * len(self._turn_ids)
            + sum(len(c) + 49 for c in self.compactions)
        )


_CARRIER_RE = re.compile(rb'"content"\s*:\s*"Tool results?: [0-9]+ results?"', re.IGNORECASE)
_HAS_TOOL_RESULTS_RE = re.compile(rb'"has_tool_results"\s*:\s*true')


# Duty-cycle throttle of the (optional, low-priority) index scan: sleep this multiple of each
# batch's busy time (1.0 = at most ~50 % CPU duty), leaving the event loop GIL headroom.
INDEX_SCAN_THROTTLE = 1.0


def scan_file_into(
    reader: Any, state: HistoryIndexState, cancel: Any = None, throttle: float | None = None
) -> None:
    """Forward scan of ``reader``'s file into ``state`` (worker-thread code). Only prefiltered
    lines are parsed; the gap between two matches is read (and classified) only while a
    compaction-group prefix is pending, so unrelated records interleaved inside a group break it
    exactly as they do in the browser's list."""
    progress = [0]
    throttle = INDEX_SCAN_THROTTLE if throttle is None else throttle

    def skip_carrier(data: bytes, start: int, end: int) -> bool:
        # A hidden tool-result carrier ("Tool results: N results", has_tool_results) is neither a
        # turn nor a group member; it only matters while a group prefix is pending (then the gap
        # read below classifies it). About a third of a tool-heavy file — worth not parsing.
        return (
            not state.group_pending
            and _HAS_TOOL_RESULTS_RE.search(data, start, end) is not None
            and _CARRIER_RE.search(data, start, end) is not None
        )

    for offset, end, record in reader.iter_records_with_offsets(
        INDEX_PREFILTERS, cancel=cancel, progress=progress, regex=True, skip_line=skip_carrier,
        include_unterminated=False, throttle=throttle,
    ):
        if state.group_pending and offset > state.end:
            _resolve_gap(reader, state, offset)
        state.apply(offset, end, record)
    if state.group_pending and progress[0] > state.end:
        _resolve_gap(reader, state, progress[0])
    state.advance(progress[0])


def _resolve_gap(reader: Any, state: HistoryIndexState, upto: int) -> None:
    """Classify the unmatched lines between ``state.end`` and ``upto`` while a group prefix is
    pending; stops as soon as the prefix resolves (the rest of the gap cannot matter)."""
    for gap_start, gap_end, gap_record in reader.read_range(state.end, upto):
        state.apply(gap_start, gap_end, gap_record)
        if not state.group_pending:
            return


def reduce_records(
    records: list[dict], *, archive: bool = False, history_id: str = ""
) -> HistoryIndexState:
    """Reduce an in-memory record list (tests, shared case file); offsets are synthetic."""
    state = HistoryIndexState(history_id, archive=archive)
    offset = 0
    for record in records:
        state.apply(offset, offset + 1, record)
        offset += 1
    return state
