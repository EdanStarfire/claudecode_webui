"""Slack Events API payload -> RelayEvent (issue #2103 stage 3a). Pure, no I/O.

Dropped before dedupe (counted, not audited per event): non-message events, a workspace other
than the relay's, every subtype except none / ``file_share`` / ``thread_broadcast`` (so edits,
deletions, bot messages and joins never reach the router: an edit can never re-trigger an
instruction), and events missing ``channel`` / ``ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..adapter import RelayMessage
from ..events import RelayEvent, is_agent_message
from ..slack_adapter import _text_from_blocks

ALLOWED_SUBTYPES = frozenset({None, "file_share", "thread_broadcast"})


@dataclass(frozen=True)
class Drop:
    reason: str


def normalize_event(
    relay_id: str, payload: dict[str, Any], workspace_id: str | None
) -> RelayEvent | Drop:
    """*payload* is the envelope's ``payload`` (an ``event_callback``)."""
    if not isinstance(payload, dict):
        return Drop("malformed")
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "message":
        return Drop("not_message")
    team = payload.get("team_id")
    if workspace_id and team and team != workspace_id:
        return Drop("wrong_workspace")
    subtype = event.get("subtype")
    if subtype not in ALLOWED_SUBTYPES:
        return Drop("subtype")
    channel, ts = event.get("channel"), event.get("ts")
    if not isinstance(channel, str) or not channel or not isinstance(ts, str) or not ts:
        return Drop("malformed")
    text = event.get("text")
    if not isinstance(text, str) or not text:
        text = _text_from_blocks(event.get("blocks"))
    thread_ts = event.get("thread_ts")
    metadata = event.get("metadata")
    files = event.get("files")
    event_obj = RelayEvent(
        relay_id=relay_id, channel=channel, ts=ts,
        user=event.get("user") if isinstance(event.get("user"), str) else None,
        text=text or "",
        thread_ts=thread_ts if isinstance(thread_ts, str) and thread_ts else None,
        channel_type=event.get("channel_type") if isinstance(event.get("channel_type"), str) else None,
        app_id=event.get("app_id") if isinstance(event.get("app_id"), str) else None,
        bot_id=event.get("bot_id") if isinstance(event.get("bot_id"), str) else None,
        metadata=metadata if isinstance(metadata, dict) else None,
        subtype=subtype, file_count=len(files) if isinstance(files, list) else 0,
    )
    event_obj.is_agent = is_agent_message(
        {"app_id": event_obj.app_id, "bot_id": event_obj.bot_id, "metadata": event_obj.metadata}
    )
    return event_obj


def event_from_message(relay_id: str, msg: RelayMessage, channel: str) -> RelayEvent | Drop:
    """A backfilled history/thread message in the same shape as a live one (the self-DM is an IM)."""
    if not msg.ts:
        return Drop("malformed")
    event = RelayEvent(
        relay_id=relay_id, channel=channel, ts=msg.ts, user=msg.user, text=msg.text or "",
        thread_ts=msg.thread_ts, channel_type="im", app_id=msg.app_id, bot_id=msg.bot_id,
        metadata=msg.metadata,
    )
    event.is_agent = is_agent_message(msg)
    return event
