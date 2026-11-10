"""Normalized relay events and agent-message detection (issue #2103).

``RelayEvent`` is the inbound stage's normalized message; ``is_agent_message`` is the
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
    app_id: str | None = None
    bot_id: str | None = None
    metadata: dict[str, Any] | None = None
    subtype: str | None = None
    file_count: int = 0
    api_app_id: str | None = None  # the receiving app (event_callback.api_app_id), live events only

    def to_dict(self) -> dict[str, Any]:
        """Persistable form (``raw`` is dropped: it can be large and is never needed again)."""
        return {
            "relay_id": self.relay_id, "channel": self.channel, "ts": self.ts, "user": self.user,
            "text": self.text, "thread_ts": self.thread_ts, "channel_type": self.channel_type,
            "is_agent": self.is_agent, "app_id": self.app_id, "bot_id": self.bot_id,
            "metadata": self.metadata, "subtype": self.subtype, "file_count": self.file_count,
            "api_app_id": self.api_app_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelayEvent:
        return cls(
            relay_id=data["relay_id"], channel=data["channel"], ts=data["ts"],
            user=data.get("user"), text=data.get("text") or "", thread_ts=data.get("thread_ts"),
            channel_type=data.get("channel_type"), is_agent=bool(data.get("is_agent")),
            app_id=data.get("app_id"), bot_id=data.get("bot_id"), metadata=data.get("metadata"),
            subtype=data.get("subtype"), file_count=int(data.get("file_count") or 0),
            api_app_id=data.get("api_app_id"),
        )


def is_agent_message(msg: RelayMessage | dict) -> bool:
    """True for anything posted through an app token or carrying our metadata marker."""
    if isinstance(msg, dict):
        app_id, bot_id, metadata = msg.get("app_id"), msg.get("bot_id"), msg.get("metadata")
    else:
        app_id, bot_id, metadata = msg.app_id, msg.bot_id, msg.metadata
    if app_id or bot_id:
        return True
    return isinstance(metadata, dict) and metadata.get("event_type") == RELAY_EVENT_TYPE
