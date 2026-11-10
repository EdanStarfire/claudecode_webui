"""Monitor matching: which monitors does an inbound event feed (issue #2103 stage 3b-i). Pure.

Order of checks per event: (1) never content (self-DM in any thread, no sender, the echo rule,
instruction-thread replies); (2) candidates by channel from the store index; (3) thread / filter
rules; (4) the monitor is active, not paused and not expired. Nothing here does I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ..events import RELAY_EVENT_TYPE, RelayEvent
from ..safety import has_agent_marker
from .constants import (
    KIND_MONITOR_CHANNEL,
    KIND_MONITOR_DMS,
    KIND_MONITOR_MENTIONS,
    KIND_MONITOR_THREAD,
)
from .store import ACTIVE


@dataclass
class MatchContext:
    owner_user_id: str | None
    self_dm: str | None
    own_app_id: str | None  # learned from the first event's api_app_id; None until known
    is_instruction_thread: Callable[[str, str], bool]
    now: float


def fold(text: str | None) -> str:
    """NFKC + casefold, the keyword comparison form."""
    return unicodedata.normalize("NFKC", text or "").casefold()


def echo_reason(
    event: RelayEvent, own_app_id: str | None, owner_user_id: str | None = None
) -> str | None:
    """Why the event is the relay's / an agent's own post (never delivered as content).

    Our app acts with the owner's own user token, so a message FROM THE OWNER that carries an
    ``app_id`` / ``bot_id`` is one of ours even before the app id has been learned (the owner's own
    typing never has either)."""
    metadata = event.metadata
    if isinstance(metadata, dict) and metadata.get("event_type") == RELAY_EVENT_TYPE:
        return "echo_metadata"
    if own_app_id and event.app_id == own_app_id:
        return "echo_app"
    if owner_user_id and event.user == owner_user_id and (event.app_id or event.bot_id):
        return "echo_owner_app"
    if has_agent_marker(event.text):
        return "echo_marker"
    return None


def never_content(event: RelayEvent, ctx: MatchContext) -> str | None:
    if ctx.self_dm and event.channel == ctx.self_dm:
        return "self_dm"  # the self-DM, any thread, is the instruction channel: never content
    if not event.user and not (event.bot_id or event.app_id):
        return "no_sender"
    reason = echo_reason(event, ctx.own_app_id, ctx.owner_user_id)
    if reason:
        return reason
    if event.thread_ts and event.thread_ts != event.ts and ctx.is_instruction_thread(
        event.channel, event.thread_ts
    ):
        return "instruction_thread"
    return None


def is_bot(event: RelayEvent) -> bool:
    return bool(event.app_id or event.bot_id or event.is_agent)


def filters_match(filters: dict[str, Any], event: RelayEvent) -> bool:
    """Keywords OR, users OR; both groups present -> AND; no filters -> everything."""
    keywords = [fold(k) for k in filters.get("keywords") or [] if k]
    users = filters.get("users") or []
    if keywords:
        text = fold(event.text)
        if not any(k in text for k in keywords):
            return False
    if users and event.user not in users:
        return False
    return True


def monitor_active(monitor: dict[str, Any], now: float) -> bool:
    if monitor.get("paused") or monitor.get("status") != ACTIVE:
        return False
    expires = monitor.get("expires_at")
    return expires is None or now < float(expires)


def mentions_owner(text: str | None, owner_user_id: str | None) -> bool:
    """``<@OWNER>`` or ``<@OWNER|name>`` in *text* (anchored on the user id, never on names).
    ``@here`` / ``@channel`` / ``@everyone`` and user-group mentions do not count."""
    if not owner_user_id or not text:
        return False
    return re.search(rf"<@{re.escape(owner_user_id)}(?:\|[^>]*)?>", text) is not None


def matches_monitor(
    event: RelayEvent, monitor: dict[str, Any], owner_user_id: str | None = None
) -> bool:
    """Scope + filter rule for one monitor (state is checked separately). The scope kinds
    (mentions / DMs) have no channel and need the owner's id; without it they never match."""
    kind = monitor.get("kind")
    if kind in (KIND_MONITOR_MENTIONS, KIND_MONITOR_DMS):
        if not owner_user_id or event.user == owner_user_id:
            return False  # the owner's own messages are never content
        if kind == KIND_MONITOR_MENTIONS:
            if not mentions_owner(event.text, owner_user_id):
                return False
        elif event.channel_type not in ("im", "mpim"):
            return False
        return filters_match(monitor.get("filters") or {}, event)
    if monitor.get("channel") != event.channel:
        return False
    if kind == KIND_MONITOR_THREAD:
        if event.thread_ts != monitor.get("thread_ts") or event.ts == monitor.get("thread_ts"):
            return False
    elif kind != KIND_MONITOR_CHANNEL:
        return False
    return filters_match(monitor.get("filters") or {}, event)


def match(event: RelayEvent, candidates: Iterable[dict[str, Any]], ctx: MatchContext) -> list[dict[str, Any]]:
    """The monitors *event* feeds (empty when it is never content)."""
    if never_content(event, ctx):
        return []
    return [
        m for m in candidates
        if monitor_active(m, ctx.now) and matches_monitor(event, m, ctx.owner_user_id)
    ]
