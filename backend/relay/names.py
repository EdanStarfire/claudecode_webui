"""Lazy, bounded in-memory name resolution for the relay (issue #2103).

User IDs (``users.info``) and conversation IDs (``conversations.info``) are stable, so resolved
names are cached for the process lifetime (no persistence), LRU-bounded. Resolution never blocks
or fails the caller: every lookup has a short timeout and falls back to the ID. Names are
Slack-user-controlled strings: they are cleaned (control/zero-width characters removed, length
capped) and are for plain-text display to the owner or for use INSIDE the untrusted wrapper
only; they never appear in non-wrapped tool output.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .adapter import RelayAdapter

MAX_CACHE = 2000
LOOKUP_TIMEOUT = 3.0
FAILURE_TTL = 60.0  # do not hammer Slack for an ID that just failed
MAX_NAME_LENGTH = 80
CONCURRENCY = 5

# control chars, zero-width/formatting (U+200B-200F), line/para separators and bidi overrides
# (U+2028-202E), word joiners / invisible operators (U+2060-2064), bidi isolates (U+2066-2069), BOM
_STRIP_RE = re.compile("[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


def clean_name(value: str | None) -> str | None:
    """Plain, bounded display text: control, zero-width and bidi characters removed."""
    if not value:
        return None
    cleaned = _STRIP_RE.sub("", value).strip()
    return cleaned[:MAX_NAME_LENGTH] or None


@dataclass(frozen=True)
class ConversationDesc:
    name: str | None = None
    is_im: bool = False
    is_mpim: bool = False
    user: str | None = None  # peer user id for IMs


class _Lru:
    def __init__(self, size: int) -> None:
        self._size = size
        self._data: OrderedDict[Any, Any] = OrderedDict()

    def get(self, key):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key, value) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._size:
            self._data.popitem(last=False)

    def __contains__(self, key) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)


class NameResolver:
    def __init__(
        self,
        *,
        max_size: int = MAX_CACHE,
        timeout: float = LOOKUP_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._users = _Lru(max_size)
        self._convs = _Lru(max_size)
        self._failed = _Lru(max_size)  # key -> retry-after time
        self._timeout = timeout
        self._clock = clock

    def _recently_failed(self, key) -> bool:
        until = self._failed.get(key)
        return until is not None and until > self._clock()

    async def _lookup(self, key, fetch: Callable[[], Awaitable[Any]]):
        if self._recently_failed(key):
            return None
        try:
            return await asyncio.wait_for(fetch(), self._timeout)
        except Exception:  # RelayError, timeout, anything: fall back to the ID
            self._failed.put(key, self._clock() + FAILURE_TTL)
            return None

    # ---------------------------------------------------------------- users

    async def user_name(self, adapter: RelayAdapter, relay_id: str, user_id: str) -> str | None:
        key = (relay_id, user_id)
        if key in self._users:
            return self._users.get(key)
        info = await self._lookup(("u", *key), lambda: adapter.user_info(user_id))
        if info is None:
            return None
        name = clean_name(info.name)
        self._users.put(key, name)
        return name

    async def user_names(
        self, adapter: RelayAdapter, relay_id: str, user_ids: Iterable[str | None], limit: int = 20
    ) -> dict[str, str | None]:
        """Names for the distinct IDs (at most *limit* lookups, a few at a time)."""
        ids = list(dict.fromkeys(u for u in user_ids if u))[:limit]
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(uid: str):
            async with sem:
                return uid, await self.user_name(adapter, relay_id, uid)

        return dict(await asyncio.gather(*(one(u) for u in ids)))

    # -------------------------------------------------------- conversations

    def seed_channel(self, relay_id: str, channel_id: str, name: str | None) -> None:
        """Record a name already known from a listing (no extra call later)."""
        self._convs.put((relay_id, channel_id), ConversationDesc(name=clean_name(name)))

    async def conversation(
        self, adapter: RelayAdapter, relay_id: str, channel_id: str
    ) -> ConversationDesc | None:
        key = (relay_id, channel_id)
        if key in self._convs:
            return self._convs.get(key)
        info = await self._lookup(("c", *key), lambda: adapter.conversation_info(channel_id))
        if info is None:
            return None
        desc = ConversationDesc(
            name=clean_name(info.name), is_im=info.is_im, is_mpim=info.is_mpim, user=info.user
        )
        self._convs.put(key, desc)
        return desc

    async def channel_name(self, adapter: RelayAdapter, relay_id: str, channel_id: str) -> str | None:
        """Plain channel name (no ``#``); None for DMs or when unresolved."""
        desc = await self.conversation(adapter, relay_id, channel_id)
        return desc.name if desc and not desc.is_im else None

    async def channel_label(
        self,
        adapter: RelayAdapter,
        relay_id: str,
        channel_id: str,
        owner_user_id: str | None = None,
    ) -> str:
        """Human label: ``#name``, ``DM with <name>``, ``your self-DM``; the ID on failure."""
        desc = await self.conversation(adapter, relay_id, channel_id)
        if desc is None:
            return channel_id
        if desc.is_im and desc.user:
            if owner_user_id and desc.user == owner_user_id:
                return "your self-DM"
            name = await self.user_name(adapter, relay_id, desc.user)
            return f"DM with {name or desc.user}"
        if desc.is_mpim:
            return f"group DM {desc.name or channel_id}"
        return f"#{desc.name}" if desc.name else channel_id
