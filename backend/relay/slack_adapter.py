"""SlackAdapter: RelayAdapter over httpx.AsyncClient (issue #2103).

Tokens are fetched per call through ``token_provider`` and are only ever placed
in the Authorization header: never logged, never in exception text.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .adapter import (
    AuthInfo,
    ConversationInfo,
    EventHandler,
    PostResult,
    RelayError,
    RelayMessage,
    SearchHit,
    SearchPage,
    ThreadPage,
    UploadResult,
    UserInfo,
)

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api/"
_MAX_RETRIES = 2
_MAX_RETRY_WAIT = 30.0
_MAX_PAGES = 20
_DIRECTORY_PAGES = 30  # 30 x 200 covers the 5000-entry directory cap

TokenProvider = Callable[[], Awaitable[str]]


def _leaf_text(node: Any) -> str:
    """Concatenated ``text`` values inside a rich_text tree (``link`` elements use ``url``)."""
    if isinstance(node, list):
        return "".join(_leaf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "user" and isinstance(node.get("user_id"), str):
        return f"<@{node['user_id']}>"  # a mention in a block-only message stays detectable
    if isinstance(node.get("text"), str):
        return node["text"]
    if node.get("type") == "link":
        return node.get("text") or node.get("url") or ""
    return _leaf_text(node.get("elements"))


def _text_from_blocks(blocks: Any) -> str:
    """Plain text of ``section`` / ``rich_text`` blocks (``context`` blocks, e.g. our footer,
    are skipped). Slack's search.messages returns ``text: ""`` for messages posted with blocks."""
    parts: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "section":
            text = (block.get("text") or {}).get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block.get("type") == "rich_text":
            parts.append(_leaf_text(block.get("elements")))
    return "\n".join(p for p in parts if p)


def _parse_message(raw: dict, channel: str | None = None) -> RelayMessage:
    return RelayMessage(
        ts=raw.get("ts", ""),
        text=raw.get("text") or _text_from_blocks(raw.get("blocks")),
        user=raw.get("user") or raw.get("username"),
        thread_ts=raw.get("thread_ts"),
        channel=channel or (raw["channel"] if isinstance(raw.get("channel"), str) else None),
        app_id=raw.get("app_id"),
        bot_id=raw.get("bot_id"),
        metadata=raw.get("metadata"),
        permalink=raw.get("permalink"),
        subtype=raw.get("subtype") if isinstance(raw.get("subtype"), str) else None,
        file_count=len(raw["files"]) if isinstance(raw.get("files"), list) else 0,
    )


def _latest_ts(raw: Any) -> str | None:
    """``latest`` is a ts string on some conversation payloads and a message object on others."""
    if isinstance(raw, dict):
        raw = raw.get("ts")
    return raw if isinstance(raw, str) and raw else None


def _parse_conversation(raw: dict) -> ConversationInfo:
    return ConversationInfo(
        id=raw.get("id", ""),
        name=raw.get("name"),
        is_im=bool(raw.get("is_im")),
        is_mpim=bool(raw.get("is_mpim")),
        is_private=bool(raw.get("is_private")),
        is_channel=bool(raw.get("is_channel")),
        user=raw.get("user"),
        last_read=raw.get("last_read"),
        unread_count=raw.get("unread_count_display", raw.get("unread_count")),
        is_member=raw.get("is_member") if isinstance(raw.get("is_member"), bool) else None,
        latest_ts=_latest_ts(raw.get("latest")),
    )


def _parse_user(user: dict, fallback_id: str) -> UserInfo:
    profile = user.get("profile") or {}
    display = profile.get("display_name") or None
    real = profile.get("real_name") or user.get("real_name") or None
    handle = user.get("name") or None
    return UserInfo(
        id=user.get("id", fallback_id),
        name=display or real or handle,
        is_bot=bool(user.get("is_bot")),
        display_name=display,
        real_name=real,
        handle=handle,
        deleted=bool(user.get("deleted")),
        tz=user.get("tz") if isinstance(user.get("tz"), str) and user.get("tz") else None,
    )


class SlackAdapter:
    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base_url: str = SLACK_API,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._sleep = sleep
        self._base_url = base_url

    # ------------------------------------------------------------------ transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=30.0)

    async def _call(
        self, method: str, params: dict[str, Any] | None = None, *, json_body: bool = False,
        retry: bool = True,
    ) -> dict:
        """POST a Slack Web API method; returns the parsed ``ok:true`` payload. ``retry=False``
        never waits and retries on a rate limit (it raises ``ratelimited`` at once)."""
        max_retries = _MAX_RETRIES if retry else 0
        token = await self._token_provider()
        headers = {"Authorization": f"Bearer {token}"}
        payload = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(max_retries + 1):
            try:
                async with self._client() as client:
                    if json_body:
                        headers["Content-Type"] = "application/json; charset=utf-8"
                        resp = await client.post(self._base_url + method, headers=headers, json=payload)
                    else:
                        resp = await client.post(self._base_url + method, headers=headers, data=payload)
            except httpx.HTTPError as e:
                # Never include str(e): keep exception text free of request detail.
                logger.warning("Slack %s transport error: %s", method, type(e).__name__)
                raise RelayError("network_error", method) from None

            retry_after = self._retry_after(resp)
            if resp.status_code == 429 and attempt < max_retries:
                await self._sleep(retry_after)
                continue
            if resp.status_code == 429:
                raise RelayError("ratelimited", method)
            if resp.status_code >= 400:
                raise RelayError(f"http_{resp.status_code}", method)
            try:
                data = resp.json()
            except ValueError:
                raise RelayError("invalid_response", method) from None
            if data.get("ok"):
                return data
            code = data.get("error", "unknown_error")
            if code == "ratelimited" and attempt < max_retries:
                await self._sleep(retry_after)
                continue
            raise RelayError(code, method)
        raise RelayError("ratelimited", method)  # pragma: no cover

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float:
        try:
            return min(max(float(resp.headers.get("Retry-After", "1")), 0.0), _MAX_RETRY_WAIT)
        except ValueError:
            return 1.0

    async def _paginate(
        self, method: str, params: dict[str, Any], items_key: str, limit: int,
        max_pages: int = _MAX_PAGES,
    ) -> tuple[list[dict], str | None]:
        """Collect up to *limit* items following ``response_metadata.next_cursor``."""
        items: list[dict] = []
        cursor = params.get("cursor")
        for _ in range(max_pages):
            page_params = {**params, "cursor": cursor, "limit": min(limit - len(items), 200)}
            data = await self._call(method, page_params)
            items.extend(data.get(items_key) or [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
            if len(items) >= limit or not cursor:
                break
        return items[:limit], cursor

    # --------------------------------------------------------------------- API

    async def auth_test(self) -> AuthInfo:
        data = await self._call("auth.test")
        return AuthInfo(
            user_id=data.get("user_id", ""),
            team_id=data.get("team_id", ""),
            team=data.get("team"),
            user=data.get("user"),
            url=data.get("url"),
        )

    async def post(
        self,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
        thread_ts: str | None = None,
        metadata: dict | None = None,
    ) -> PostResult:
        data = await self._call(
            "chat.postMessage",
            {"channel": channel, "text": text, "blocks": blocks, "thread_ts": thread_ts, "metadata": metadata},
            json_body=True,
        )
        return PostResult(channel=data.get("channel", channel), ts=data.get("ts", ""))

    async def read_thread(
        self, channel: str, thread_ts: str, limit: int = 100, cursor: str | None = None,
        oldest: str | None = None, latest: str | None = None, inclusive: bool = False,
    ) -> ThreadPage:
        items, next_cursor = await self._paginate(
            "conversations.replies",
            {"channel": channel, "ts": thread_ts, "include_all_metadata": "true", "cursor": cursor,
             "oldest": oldest, "latest": latest, "inclusive": "true" if inclusive else None},
            "messages",
            limit,
        )
        return ThreadPage([_parse_message(m, channel) for m in items], next_cursor)

    async def read_history(
        self, channel: str, limit: int = 100, oldest: str | None = None, cursor: str | None = None,
        latest: str | None = None, inclusive: bool = False,
    ) -> ThreadPage:
        items, next_cursor = await self._paginate(
            "conversations.history",
            {"channel": channel, "oldest": oldest, "latest": latest,
             "inclusive": "true" if inclusive else None, "include_all_metadata": "true",
             "cursor": cursor},
            "messages",
            limit,
        )
        return ThreadPage([_parse_message(m, channel) for m in items], next_cursor)

    async def search(
        self, query: str, in_channels: list[str] | None = None, limit: int = 20
    ) -> list[RelayMessage]:
        full_query = query
        for channel in in_channels or []:
            full_query += f" in:<#{channel}>"
        data = await self._call("search.messages", {"query": full_query, "count": min(limit, 100)})
        matches = (data.get("messages") or {}).get("matches") or []
        results = []
        for raw in matches[:limit]:
            channel_id = (raw.get("channel") or {}).get("id")
            results.append(_parse_message({**raw, "channel": channel_id}, channel_id))
        return results

    async def search_page(
        self, query: str, page: int = 1, count: int = 100, sort: str = "timestamp",
        sort_dir: str = "desc",
    ) -> SearchPage:
        """One page of ``search.messages`` with the sort the caller asks for (inbound mentions
        backfill: newest first). The hits are search metadata only: hydrate before trusting them."""
        data = await self._call(
            "search.messages",
            {"query": query, "count": min(count, 100), "page": page, "sort": sort, "sort_dir": sort_dir},
        )
        block = data.get("messages") or {}
        hits = []
        for raw in block.get("matches") or []:
            chan = raw.get("channel") if isinstance(raw.get("channel"), dict) else {}
            channel_id = chan.get("id")
            hits.append(SearchHit(
                _parse_message({**raw, "channel": channel_id}, channel_id),
                is_im=bool(chan.get("is_im")), is_mpim=bool(chan.get("is_mpim")),
            ))
        paging = block.get("paging") or {}
        return SearchPage(hits, int(paging.get("page") or page), int(paging.get("pages") or 1))

    async def upload(
        self,
        channel: str,
        path: Path,
        thread_ts: str | None = None,
        comment: str | None = None,
        title: str | None = None,
    ) -> UploadResult:
        content = await asyncio.to_thread(Path(path).read_bytes)
        filename = Path(path).name
        slot = await self._call("files.getUploadURLExternal", {"filename": filename, "length": len(content)})
        try:
            async with self._client() as client:
                # The pre-signed upload URL is a different host: no Authorization header.
                resp = await client.post(slot["upload_url"], content=content)
        except httpx.HTTPError as e:
            logger.warning("Slack file upload transport error: %s", type(e).__name__)
            raise RelayError("network_error", "files.upload_url") from None
        if resp.status_code >= 400:
            raise RelayError(f"http_{resp.status_code}", "files.upload_url")
        file_id = slot["file_id"]
        await self._call(
            "files.completeUploadExternal",
            {
                "files": json.dumps([{"id": file_id, "title": title or filename}]),
                "channel_id": channel,
                "thread_ts": thread_ts,
                "initial_comment": comment,
            },
        )
        return UploadResult(file_id=file_id, title=title or filename)

    async def react(self, channel: str, ts: str, name: str, *, retry: bool = True) -> None:
        await self._call(
            "reactions.add", {"channel": channel, "timestamp": ts, "name": name},
            json_body=True, retry=retry,
        )

    async def unreact(self, channel: str, ts: str, name: str, *, retry: bool = True) -> None:
        await self._call(
            "reactions.remove", {"channel": channel, "timestamp": ts, "name": name},
            json_body=True, retry=retry,
        )

    async def mark_read(self, channel: str, ts: str) -> None:
        await self._call("conversations.mark", {"channel": channel, "ts": ts}, json_body=True)

    async def conversation_info(self, channel: str) -> ConversationInfo:
        data = await self._call("conversations.info", {"channel": channel})
        return _parse_conversation(data.get("channel") or {})

    async def list_conversations(
        self, types: str = "public_channel,private_channel,mpim,im", limit: int = 200
    ) -> list[ConversationInfo]:
        items, _ = await self._paginate(
            "users.conversations", {"types": types, "exclude_archived": "true"}, "channels", limit
        )
        return [_parse_conversation(c) for c in items]

    async def list_channels(
        self, types: str = "public_channel,private_channel", limit: int = 1000
    ) -> list[ConversationInfo]:
        items, _ = await self._paginate(
            "conversations.list", {"types": types, "exclude_archived": "true"}, "channels", limit,
            max_pages=_DIRECTORY_PAGES,
        )
        return [_parse_conversation(c) for c in items]

    async def list_users(self, limit: int = 5000) -> list[UserInfo]:
        items, _ = await self._paginate(
            "users.list", {}, "members", limit, max_pages=_DIRECTORY_PAGES
        )
        return [_parse_user(u, u.get("id", "")) for u in items]

    async def open_dm(self, user_id: str) -> str:
        data = await self._call("conversations.open", {"users": user_id}, json_body=True)
        return (data.get("channel") or {}).get("id", "")

    async def user_info(self, user_id: str) -> UserInfo:
        data = await self._call("users.info", {"user": user_id})
        return _parse_user(data.get("user") or {}, user_id)

    async def start_events(self, handler: EventHandler) -> None:
        raise NotImplementedError("Slack event streaming arrives with the inbound stage")
