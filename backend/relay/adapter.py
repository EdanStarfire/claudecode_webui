"""RelayAdapter protocol and normalized result types (issue #2103)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class RelayError(Exception):
    """An adapter call failed. ``code`` is the provider error code (e.g. ``channel_not_found``).

    The message never contains token material.
    """

    def __init__(self, code: str, method: str | None = None):
        self.code = code
        self.method = method
        super().__init__(f"{method}: {code}" if method else code)


@dataclass
class RelayMessage:
    ts: str
    text: str = ""
    user: str | None = None
    thread_ts: str | None = None
    channel: str | None = None
    app_id: str | None = None
    bot_id: str | None = None
    metadata: dict[str, Any] | None = None
    permalink: str | None = None


@dataclass
class AuthInfo:
    user_id: str
    team_id: str
    team: str | None = None
    user: str | None = None
    url: str | None = None


@dataclass
class PostResult:
    channel: str
    ts: str


@dataclass
class UploadResult:
    file_id: str
    title: str | None = None


@dataclass
class ThreadPage:
    messages: list[RelayMessage] = field(default_factory=list)
    next_cursor: str | None = None


@dataclass
class ConversationInfo:
    id: str
    name: str | None = None
    is_im: bool = False
    is_mpim: bool = False
    is_private: bool = False
    is_channel: bool = False
    user: str | None = None  # peer user id for IMs
    last_read: str | None = None
    unread_count: int | None = None


@dataclass
class UserInfo:
    id: str
    name: str | None = None  # best display name: display_name, else real_name, else handle
    is_bot: bool = False
    display_name: str | None = None
    real_name: str | None = None
    handle: str | None = None  # the Slack ``name`` field
    deleted: bool = False


EventHandler = Callable[[Any], Awaitable[None]]


@runtime_checkable
class RelayAdapter(Protocol):
    async def auth_test(self) -> AuthInfo: ...

    async def post(
        self,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
        thread_ts: str | None = None,
        metadata: dict | None = None,
    ) -> PostResult: ...

    async def read_thread(
        self, channel: str, thread_ts: str, limit: int = 100, cursor: str | None = None
    ) -> ThreadPage: ...

    async def read_history(
        self, channel: str, limit: int = 100, oldest: str | None = None, cursor: str | None = None
    ) -> ThreadPage: ...

    async def search(
        self, query: str, in_channels: list[str] | None = None, limit: int = 20
    ) -> list[RelayMessage]: ...

    async def upload(
        self,
        channel: str,
        path: Path,
        thread_ts: str | None = None,
        comment: str | None = None,
        title: str | None = None,
    ) -> UploadResult: ...

    async def react(self, channel: str, ts: str, name: str) -> None: ...

    async def mark_read(self, channel: str, ts: str) -> None: ...

    async def conversation_info(self, channel: str) -> ConversationInfo: ...

    async def list_conversations(
        self, types: str = "public_channel,private_channel,mpim,im", limit: int = 200
    ) -> list[ConversationInfo]: ...

    async def list_channels(
        self, types: str = "public_channel,private_channel", limit: int = 1000
    ) -> list[ConversationInfo]:
        """Workspace channels (``conversations.list``): includes public channels not joined."""
        ...

    async def list_users(self, limit: int = 5000) -> list[UserInfo]:
        """Workspace members (``users.list``), deleted ones flagged."""
        ...

    async def open_dm(self, user_id: str) -> str: ...

    async def user_info(self, user_id: str) -> UserInfo: ...

    async def start_events(self, handler: EventHandler) -> None:
        """Reserved for the inbound stage; not implemented in outbound-1a."""
        ...
