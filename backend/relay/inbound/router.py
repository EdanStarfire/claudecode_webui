"""Instruction classification: the inbound trust rule (issue #2103 stage 3a). Pure, no I/O.

An event is an instruction CANDIDATE only if ALL hold:
  1. its channel is the relay's resolved self-DM (never taken from event text);
  2. its sender is the relay's owner;
  3. no app_id / bot_id and no agent metadata (``is_agent``);
  4. no agent marker in the text;
  5. channel_type is ``im``.
Everything else is third-party content and is dropped in 3a (3b turns subscribed content into
fenced deliveries). Thread replies route through an owned thread record; top-level messages
through a ``!handle`` found outside Slack angle tokens.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..events import RelayEvent
from ..safety import RESERVED_HANDLES, TRIGGER_RE, has_agent_marker

KIND_INSTRUCTION = "instruction"
KIND_REPLY = "reply"  # nothing delivered; a marked error reply is posted
KIND_IGNORE = "ignore"

_ANGLE_RE = re.compile(r"<[^<>]*>")


@dataclass
class Decision:
    kind: str
    reason: str | None = None  # ignore reason / error-reply code
    session_id: str | None = None
    handle: str | None = None
    text: str = ""  # the instruction text (handle removed for a top-level instruction)
    thread_ts: str | None = None  # the Slack thread the answer belongs to
    top_level: bool = False
    thread_record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _segments(text: str) -> list[tuple[str, bool]]:
    """Split into (segment, is_slack_token) so ``<!here>``-style tokens are never scanned."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _ANGLE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def find_handles(text: str) -> list[str]:
    """Distinct non-reserved ``!handle`` handles (lowercased) outside angle tokens, in order."""
    found: list[str] = []
    for segment, is_token in _segments(text):
        if is_token:
            continue
        for m in TRIGGER_RE.finditer(segment):
            handle = m.group(1).lower()
            if handle not in RESERVED_HANDLES and handle not in found:
                found.append(handle)
    return found


def remove_handle(text: str, handle: str) -> str:
    """Remove every ``!handle`` occurrence (outside angle tokens) and collapse whitespace."""
    pattern = re.compile(rf"(?<![\w!])!{re.escape(handle)}(?![\w-])", re.IGNORECASE)
    parts = [seg if is_token else pattern.sub(" ", seg) for seg, is_token in _segments(text)]
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def is_candidate(event: RelayEvent, self_dm: str | None, owner: str | None) -> str | None:
    """None when the event is an instruction candidate, else the drop reason."""
    if not self_dm or event.channel != self_dm or event.channel_type != "im":
        return "not_self_dm"
    if not owner or event.user != owner:
        return "not_owner"
    if event.is_agent or event.app_id or event.bot_id:
        return "agent_message"
    if has_agent_marker(event.text):
        return "marker"
    return None


def classify(
    event: RelayEvent,
    *,
    self_dm: str | None,
    owner: str | None,
    find_thread: Callable[[str, str], dict[str, Any] | None],
    resolve_handle: Callable[[str], str | None],
) -> Decision:
    reason = is_candidate(event, self_dm, owner)
    if reason:
        return Decision(KIND_IGNORE, reason)

    if event.thread_ts and event.thread_ts != event.ts:  # thread reply
        record = find_thread(event.channel, event.thread_ts)
        if record is None:
            return Decision(KIND_IGNORE, "unowned_thread")
        text = event.text.strip()
        if not text and not event.file_count:
            return Decision(KIND_IGNORE, "empty")
        return Decision(
            KIND_INSTRUCTION, session_id=record["session_id"], text=text,
            thread_ts=event.thread_ts, thread_record_id=record["id"],
        )

    handles = find_handles(event.text)
    if not handles:
        return Decision(KIND_IGNORE, "no_handle")
    if len(handles) > 1:
        return Decision(KIND_REPLY, "ambiguous_handle", thread_ts=event.ts, top_level=True)
    handle = handles[0]
    session_id = resolve_handle(handle)
    if session_id is None:
        return Decision(KIND_REPLY, "unknown_handle", thread_ts=event.ts, top_level=True)
    text = remove_handle(event.text, handle)
    if not text and not event.file_count:
        return Decision(KIND_REPLY, "empty_instruction", handle=handle, thread_ts=event.ts,
                        top_level=True)
    return Decision(
        KIND_INSTRUCTION, session_id=session_id, handle=handle, text=text, thread_ts=event.ts,
        top_level=True,
    )
