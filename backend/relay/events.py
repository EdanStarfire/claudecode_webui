"""Normalized relay events and agent-message detection (issue #2103).

``RelayEvent`` is reserved for the inbound stage; ``is_agent_message`` is the
echo-protection predicate (no sent-message ledger needed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapter import RelayMessage

RELAY_EVENT_TYPE = "cc_webui_agent_message"


@dataclass
class RelayEvent:
    relay_id: str
    channel: str
    ts: str
    user: str | None = None
    text: str = ""
    thread_ts: str | None = None
    channel_type: str | None = None  # "im" | "mpim" | "channel" | "group"
    is_agent: bool = False
    raw: dict[str, Any] | None = None


def is_agent_message(msg: RelayMessage | dict) -> bool:
    """True for anything posted through an app token or carrying our metadata marker."""
    if isinstance(msg, dict):
        app_id, bot_id, metadata = msg.get("app_id"), msg.get("bot_id"), msg.get("metadata")
    else:
        app_id, bot_id, metadata = msg.app_id, msg.bot_id, msg.metadata
    if app_id or bot_id:
        return True
    return isinstance(metadata, dict) and metadata.get("event_type") == RELAY_EVENT_TYPE
