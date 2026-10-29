"""Whole-history search matching (issue #2030, stage 3 PR 3B).

Pure matching helpers for the server-side search endpoint. The browser's ``findMatches``
(``frontend/src/search/messageSearch.js``) is the ground truth: a case-insensitive SUBSTRING
(no regex) over a message record's ``content`` and ``metadata.thinking_content`` and, for each
``metadata.tool_uses`` entry whose tool call exists, the tool's ``name``, ``JSON.stringify(input)``
and ``JSON.stringify(result.content)``. Matches are emitted per owning message in that order and
carry the ``occurrenceIndex`` (how many earlier matches share the message). Offsets are UTF-16
code units, like JavaScript string indices.

The file scan itself lives in ``FileMessageHistoryReader.search`` (the reader seam: a database
implementation maps it to FTS); this module owns query normalization, the byte prefilter and the
per-record matching so the two cannot drift.
"""

import base64
import functools
import json
import re
import zlib
from dataclasses import dataclass, field
from typing import Any

from .task_list import _truthy

# One message can hold thousands of occurrences (a 500 KB tool result of repeated words); a page
# must stay small, so a message reports at most this many matches (``more_in_message`` marks the
# cut). Everything below the cap is exactly findMatches' list.
MAX_HITS_PER_MESSAGE = 100
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 256
SNIPPET_RADIUS = 40

DEFAULT_MAX_RESULTS = 50
DEFAULT_MAX_SCAN_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_WALL_S = 2.0

# JS `\s` (what String.prototype.trim strips); Python's str.strip() differs.
_JS_WS_CHARS = (
    "\t\n\u000b\u000c\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
)
_ASTRAL_RE = re.compile("[\U00010000-\U0010ffff]")
_INT_KEY_RE = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")

# Raw-line forms that differ from the browser's re-serialization (`JSON.stringify` of a parsed
# tool input / result): `, ` and `: ` separators and number formatting (`1.0`, `1e-05`). A query
# that could straddle or touch them cannot be prefiltered on raw bytes.
_REFORMAT_RISK_RE = re.compile(r"[,:]|[0-9]\.|\.[0-9]|[0-9]e|e[+-]?[0-9]")


class SearchQueryError(ValueError):
    """The query is unusable (route answers 400 with ``detail`` = the message)."""


@dataclass(frozen=True)
class SearchCaps:
    """Per-page bounds. ``grace`` x the byte/wall caps is the hard limit that applies only while
    a tool hit is still waiting for its parent message (see ``FileMessageHistoryReader.search``)."""

    max_results: int = DEFAULT_MAX_RESULTS
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES
    max_wall_s: float = DEFAULT_MAX_WALL_S
    grace: float = 2.0


@dataclass
class SearchPage:
    results: list[dict[str, Any]]
    next_cursor: str | None
    exhausted: bool
    history_id: str
    scanned_bytes: int
    scanned_fraction: float
    total_bytes: int
    # Tool hits whose parent message could not be reached within the hard grace limits.
    dropped_tool_hits: int = 0
    stopped_by: str | None = None  # "results" | "bytes" | "time" | None (exhausted / cancelled)
    cancelled: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "results": self.results,
            "next_cursor": self.next_cursor,
            "exhausted": self.exhausted,
            "history_id": self.history_id,
            "scanned_bytes": self.scanned_bytes,
            "scanned_fraction": round(self.scanned_fraction, 4),
            "total_bytes": self.total_bytes,
            "dropped_tool_hits": self.dropped_tool_hits,
            "superseded": False,
        }


# ---------------------------------------------------------------------- UTF-16 helpers


def utf16_len(text: str) -> int:
    return len(text) + len(_ASTRAL_RE.findall(text))


def _u16_index(text: str, idx: int) -> int:
    """UTF-16 index of code-point index ``idx`` of ``text``."""
    return idx + len(_ASTRAL_RE.findall(text, 0, idx))


def _slice_u16(text: str, start: int, end: int) -> str:
    if not _ASTRAL_RE.search(text):
        return text[start:end]
    raw = text.encode("utf-16-le", "surrogatepass")
    return raw[start * 2 : end * 2].decode("utf-16-le", "replace")


# ---------------------------------------------------------------------- JSON.stringify


def _js_number(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return "null"
    if value == 0:
        return "0"
    if value == int(value) and abs(value) < 2**53:
        return str(int(value))
    text = repr(value)
    if "e" not in text and "E" not in text:
        return text
    mantissa, exp = text.lower().split("e")
    sign = "-" if mantissa.startswith("-") else ""
    digits = mantissa.lstrip("-").replace(".", "").rstrip("0") or "0"
    n = int(exp) + 1  # value = 0.<digits> x 10^n
    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    exp_text = ("+" if e >= 0 else "-") + str(abs(e))
    return sign + (digits if k == 1 else digits[0] + "." + digits[1:]) + "e" + exp_text


def js_stringify(value: Any) -> str | None:
    """``JSON.stringify(value)`` for a parsed-JSON value (None where JS would throw)."""
    try:
        return _stringify(value, 0)
    except (RecursionError, ValueError):
        return None


def _stringify(value: Any, depth: int) -> str:
    if depth > 200:
        raise ValueError("too deep")
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value) if abs(value) <= 2**53 else _js_number(float(value))
    if isinstance(value, float):
        return _js_number(value)
    if isinstance(value, list):
        return "[" + ",".join(_stringify(v, depth + 1) for v in value) + "]"
    if isinstance(value, dict):
        # JS orders integer-like keys ascending first, then string keys in insertion order.
        int_keys = sorted((k for k in value if _INT_KEY_RE.match(k) and int(k) < 2**32 - 1), key=int)
        int_set = set(int_keys)
        keys = int_keys + [k for k in value if k not in int_set]
        return (
            "{"
            + ",".join(
                json.dumps(k, ensure_ascii=False) + ":" + _stringify(value[k], depth + 1)
                for k in keys
            )
            + "}"
        )
    raise ValueError("unserializable")


def _searchable(value: Any, *, stringify_objects: bool = False) -> str | None:
    """``toSearchableString`` (messageSearch.js)."""
    if isinstance(value, str):
        return value
    if stringify_objects and isinstance(value, dict | list):
        return js_stringify(value)
    return None


# ---------------------------------------------------------------------- matcher


class QueryMatcher:
    """Normalized query + the cheapest valid line prefilter.

    ``mode``: ``"bytes"`` (ASCII query, compiled bytes regex over the raw line), ``"lines"``
    (non-ASCII query without quote/backslash/control/reformat risk: decode the line and compare
    lower-cased), ``"parse"`` (anything else: every line is parsed). The prefilter only ever
    produces candidates; ``field_matches`` decides.
    """

    def __init__(self, query: str):
        trimmed = (query or "").strip(_JS_WS_CHARS)
        length = utf16_len(trimmed)
        if length < MIN_QUERY_LENGTH:
            raise SearchQueryError("query_too_short")
        if length > MAX_QUERY_LENGTH:
            raise SearchQueryError("query_too_long")
        self.length = length  # JS `trimmed.length`
        self.lower = trimmed.lower()
        unsafe = any(c == '"' or c == "\\" or ord(c) < 0x20 for c in trimmed) or bool(
            _REFORMAT_RISK_RE.search(self.lower)
        )
        self.pattern: re.Pattern[bytes] | None = None
        self.needle: bytes = b""
        if unsafe:
            self.mode = "parse"
        elif self.lower.isascii():
            self.mode = "bytes"
            self.pattern = _ascii_pattern(self.lower)
            self.needle = self.lower.encode()
        else:
            self.mode = "lines"

    @property
    def key(self) -> str:
        return self.lower

    def line_candidate(self, line: bytes) -> bool:
        """For ``lines`` mode (cheap decode + compare); other modes never call this."""
        return self.lower in line.decode("utf-8", "replace").lower()

    def field_matches(
        self, text: str | None, field_name: str, tool_id: str | None,
        limit: int = MAX_HITS_PER_MESSAGE + 1,
    ) -> list[dict]:
        """Up to ``limit`` (overlapping) matches of the query in ``text``, as findMatches'
        ``pushFieldMatches`` minus ``occurrenceIndex`` (assigned when the owning message is
        emitted)."""
        if not isinstance(text, str) or not text:
            return []
        lower_text = text.lower()
        needle = self.lower
        idx = lower_text.find(needle)
        if idx == -1:
            return []
        out: list[dict] = []
        # No astral characters (the overwhelmingly common case): code-point indices ARE UTF-16
        # indices, so offsets and slices need no per-hit conversion (a per-hit scan of a 500 KB
        # tool result would hold the GIL for tens of ms).
        bmp = not _ASTRAL_RE.search(text)  # (lower-casing never creates astral characters)
        total = len(text) if bmp else utf16_len(text)
        while idx != -1 and len(out) < limit:
            start = idx if bmp else _u16_index(lower_text, idx)
            lo, hi = max(0, start - SNIPPET_RADIUS), min(total, start + self.length + SNIPPET_RADIUS)
            out.append(
                {
                    "tool_id": tool_id,
                    "field": field_name,
                    "match_start": start,
                    "match_length": self.length,
                    "snippet": text[lo:hi] if bmp else _slice_u16(text, lo, hi),
                }
            )
            idx = lower_text.find(needle, idx + 1)
        return out


# Characters whose JS lower-casing yields an ASCII letter although they are not ASCII.
_ASCII_LOWER_ALIASES = {"k": b"\xe2\x84\xaa", "i": b"\xc4\xb0"}  # KELVIN SIGN, U+0130
# A block holding none of them can be searched with the (much faster) lower-case + ``find``.
ALIAS_BYTES = tuple(_ASCII_LOWER_ALIASES.values())


@functools.lru_cache(maxsize=64)
def _ascii_pattern(lower_query: str) -> "re.Pattern[bytes]":
    parts: list[bytes] = []
    for ch in lower_query:
        if ch.isalpha():
            cls = b"[" + ch.encode() + ch.upper().encode() + b"]"
            alias = _ASCII_LOWER_ALIASES.get(ch)
            parts.append(b"(?:" + cls + b"|" + re.escape(alias) + b")" if alias else cls)
        else:
            parts.append(re.escape(ch.encode()))
    return re.compile(b"".join(parts))


# ---------------------------------------------------------------------- record matching


def tool_call_hits(matcher: QueryMatcher, record: dict) -> list[dict]:
    """Hits of one ``tool_call`` record's name / input / result (tool-order, no occurrence)."""
    tool_id = record.get("tool_use_id")
    # The browser stores `result = {content: record.result}` and lets a truthy `error` replace the
    # content (message.js `_applyToolCallRecord`); the searched text is that `content`.
    error = record.get("error")
    content = error if _truthy(error) else record.get("result")
    hits = matcher.field_matches(_searchable(record.get("name")), "tool_name", tool_id)
    for name, value in (("tool_input", record.get("input")), ("tool_result", content)):
        if len(hits) > MAX_HITS_PER_MESSAGE:
            break
        text = _searchable(value, stringify_objects=True)
        hits += matcher.field_matches(text, name, tool_id, MAX_HITS_PER_MESSAGE + 1 - len(hits))
    return hits


def message_hits(matcher: QueryMatcher, record: dict) -> list[dict]:
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    hits = matcher.field_matches(_searchable(record.get("content")), "content", None)
    if len(hits) <= MAX_HITS_PER_MESSAGE:
        hits += matcher.field_matches(
            _searchable(metadata.get("thinking_content")), "thinking", None,
            MAX_HITS_PER_MESSAGE + 1 - len(hits),
        )
    return hits


def tool_use_ids(record: dict) -> list[str]:
    metadata = record.get("metadata")
    uses = metadata.get("tool_uses") if isinstance(metadata, dict) else None
    if not isinstance(uses, list):
        return []
    return [
        tu["id"] for tu in uses if isinstance(tu, dict) and isinstance(tu.get("id"), str)
    ]


def finalize_group(record: dict, hits: list[dict], message_id: str | None) -> list[dict]:
    """Assign ``occurrenceIndex`` in emission order and attach the message context. Each hit's
    ``cursor`` / ``size_hint`` is the start offset of the MATCHING record (``hit["offset"]``): the
    message record for message fields, the ``tool_call`` record for tool fields (an upper bound
    for the parent message, which the browser's jump loads past). A group over
    ``MAX_HITS_PER_MESSAGE`` is cut and its last hit carries ``more_in_message``."""
    timestamp = record.get("timestamp")
    truncated = len(hits) > MAX_HITS_PER_MESSAGE
    out = [
        {
            "message_id": message_id,
            "tool_id": hit["tool_id"],
            "field": hit["field"],
            "match_start": hit["match_start"],
            "match_length": hit["match_length"],
            "snippet": hit["snippet"],
            "occurrence_index": i,
            "cursor": f"b:{hit['offset']}",
            "size_hint": hit["offset"],
            "type": record.get("type"),
            "timestamp": timestamp if isinstance(timestamp, int | float) else None,
        }
        for i, hit in enumerate(hits[:MAX_HITS_PER_MESSAGE])
    ]
    if truncated:
        out[-1]["more_in_message"] = True
    return out


# ---------------------------------------------------------------------- search cursor

_SEARCH_PREFIX = "s1:"
MAX_CARRY_TOOLS = 20
MAX_CARRY_HITS = 100  # total hits riding in a continuation cursor (keeps it a few KB)
MAX_PENDING_TOOLS = 1000


@dataclass
class SearchCursor:
    history_id: str
    offset: int
    pending: dict[str, list[dict]] = field(default_factory=dict)


def encode_search_cursor(history_id: str, offset: int, pending: dict[str, list[dict]]) -> str:
    base = f"{_SEARCH_PREFIX}{history_id}:{offset}"
    if not pending:
        return base
    blob = zlib.compress(json.dumps(pending, ensure_ascii=False, separators=(",", ":")).encode())
    return base + ":" + base64.urlsafe_b64encode(blob).decode().rstrip("=")


def decode_search_cursor(cursor: str) -> SearchCursor | None:
    """None when ``cursor`` is not a search cursor / is malformed (caller raises 409)."""
    if not isinstance(cursor, str) or not cursor.startswith(_SEARCH_PREFIX):
        return None
    parts = cursor[len(_SEARCH_PREFIX):].split(":")
    if len(parts) not in (2, 3) or not parts[1].isascii() or not parts[1].isdigit():
        return None
    pending: dict[str, list[dict]] = {}
    if len(parts) == 3:
        try:
            padded = parts[2] + "=" * (-len(parts[2]) % 4)
            # Bounded inflate: a cursor is client-supplied.
            raw = zlib.decompressobj().decompress(base64.urlsafe_b64decode(padded), 1 << 20)
            decoded = json.loads(raw)
        except (ValueError, zlib.error):
            return None
        if not isinstance(decoded, dict) or len(decoded) > MAX_CARRY_TOOLS:
            return None
        for tool_id, hits in decoded.items():
            if not isinstance(hits, list) or not all(
                isinstance(h, dict) and {"field", "match_start", "match_length", "snippet", "offset"} <= h.keys()
                for h in hits
            ):
                return None
            pending[tool_id] = hits
    return SearchCursor(parts[0], int(parts[1]), pending)
