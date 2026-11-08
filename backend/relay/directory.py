"""Relay directory: cached conversations and people, name lookup and ``find_channels`` search
(issue #2103 outbound-1c-ii; reused by the conversations route of stage 2b).

Two snapshots per relay: conversations (``conversations.list`` for public/private channels, so
channels the owner has not joined resolve, plus ``users.conversations`` for DMs/group DMs) and
people (``users.list``). Each snapshot has a TTL, is bounded, is refreshed single-flight, keeps
the last good copy when a refresh fails (stale-on-error) and backs off after a failure. Forced
refreshes (a name that is not in the snapshot) are rate-limited. Names are Slack-user-controlled:
they are cleaned here and only ever returned to the agent inside the untrusted wrapper.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .adapter import ConversationInfo, RelayError, UserInfo
from .names import NameResolver, clean_name
from .policy import DENY, EffectivePolicy
from .safety import safe_error_code

if TYPE_CHECKING:
    from .gate import RelayContext

KINDS = ("public", "private", "dm", "group_dm", "person")
DEFAULT_KINDS = ("public", "private", "dm", "group_dm")  # people only by explicit kind + query
MAX_ENTRIES = 5000
CONVERSATION_TTL = 300.0
USER_TTL = 600.0
FAILURE_BACKOFF = 60.0
MIN_FORCE_INTERVAL = 10.0  # a name missing from the snapshot refreshes it at most this often
MIN_PERSON_QUERY = 2
MAX_PAGE = 50
DEFAULT_PAGE = 20
MAX_CANDIDATES = 10

_OFFSET_CURSOR_RE = re.compile(r"^o:(\d{1,6})\Z")
_COLLAPSE_RE = re.compile(r"\s+")


def fold(value: str | None) -> str:
    """Case- and diacritic-insensitive comparison form (NFKC, casefold, accents removed)."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return _COLLAPSE_RE.sub(" ", text).strip()


class DirectoryLookupError(Exception):
    """Name lookup failed; *candidates* (id/kind/name dicts, Slack-controlled) are for the
    untrusted wrapper only."""

    def __init__(self, code: str, message: str, candidates: list[dict[str, Any]] | None = None):
        self.code = code
        self.message = message
        self.candidates = candidates
        super().__init__(message)


@dataclass(frozen=True)
class DirectoryEntry:
    id: str
    kind: str  # one of KINDS
    name: str | None
    peer: str | None = None  # dm: the other user's ID
    is_bot: bool = False
    aliases: tuple[str, ...] = ()  # person: folded display name, real name, handle


@dataclass
class _Snapshot:
    items: list[DirectoryEntry] | None = None
    loaded_at: float = float("-inf")
    failed_until: float = float("-inf")
    error: str = "unavailable"
    truncated: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class DirectoryPage:
    entries: list[dict[str, Any]]
    next_cursor: str | None = None
    truncated: bool = False


def _conversation_kind(c: ConversationInfo) -> str:
    if c.is_im:
        return "dm"
    if c.is_mpim:
        return "group_dm"
    return "private" if c.is_private else "public"


class RelayDirectory:
    def __init__(
        self,
        names: NameResolver,
        *,
        clock: Callable[[], float] = time.monotonic,
        conversation_ttl: float = CONVERSATION_TTL,
        user_ttl: float = USER_TTL,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self._names = names
        self._clock = clock
        self._conversation_ttl = conversation_ttl
        self._user_ttl = user_ttl
        self._max = max_entries
        self._snapshots: dict[tuple[str, str], _Snapshot] = {}

    # ------------------------------------------------------------- snapshots

    async def _snapshot(
        self,
        relay_id: str,
        kind: str,
        ttl: float,
        fetch: Callable[[], Awaitable[tuple[list[DirectoryEntry], bool]]],
        *,
        force: bool = False,
    ) -> _Snapshot:
        snap = self._snapshots.setdefault((relay_id, kind), _Snapshot())
        window = MIN_FORCE_INTERVAL if force else ttl
        if snap.items is not None and self._clock() - snap.loaded_at < window:
            return snap
        seen = (snap.loaded_at, snap.failed_until)
        async with snap.lock:
            # Single flight: whoever held the lock already refreshed (or failed) for us.
            if (snap.loaded_at, snap.failed_until) != seen or self._clock() < snap.failed_until:
                if snap.items is not None:
                    return snap
                raise RelayError(snap.error, "directory")
            try:
                items, truncated = await fetch()
            except RelayError as e:
                snap.failed_until = self._clock() + FAILURE_BACKOFF
                snap.error = safe_error_code(e.code)
                if snap.items is not None:
                    return snap
                raise
            snap.items, snap.truncated = items, truncated
            snap.loaded_at, snap.failed_until = self._clock(), float("-inf")
        return snap

    async def _conversations(self, ctx: RelayContext, *, force: bool = False) -> _Snapshot:
        async def fetch() -> tuple[list[DirectoryEntry], bool]:
            channels = await ctx.adapter.list_channels("public_channel,private_channel", self._max)
            try:
                dms = await ctx.adapter.list_conversations("im,mpim", self._max)
            except RelayError:  # DMs are optional for channel-name lookups
                dms = []
            seen: dict[str, DirectoryEntry] = {}
            for c in [*channels, *dms]:
                if not c.id or c.id in seen:
                    continue
                kind = _conversation_kind(c)
                entry = DirectoryEntry(
                    id=c.id, kind=kind,
                    name=None if kind == "dm" else clean_name(c.name),
                    peer=c.user if kind == "dm" else None,
                )
                seen[c.id] = entry
                if kind in ("public", "private") and entry.name:
                    self._names.seed_channel(ctx.relay_id, c.id, entry.name)
            items = list(seen.values())
            return items[: self._max], len(items) > self._max

        return await self._snapshot(
            ctx.relay_id, "conversations", self._conversation_ttl, fetch, force=force
        )

    async def _people(self, ctx: RelayContext, *, force: bool = False) -> _Snapshot:
        async def fetch() -> tuple[list[DirectoryEntry], bool]:
            users = await ctx.adapter.list_users(self._max + 1)
            entries = [self._person(u) for u in users if u.id and not u.deleted]
            for e in entries:
                if e.name:
                    self._names.seed_user(ctx.relay_id, e.id, e.name)
            return entries[: self._max], len(users) > self._max

        return await self._snapshot(ctx.relay_id, "people", self._user_ttl, fetch, force=force)

    @staticmethod
    def _person(u: UserInfo) -> DirectoryEntry:
        name = clean_name(u.name)
        aliases = tuple(dict.fromkeys(
            f for f in (fold(clean_name(a)) for a in (u.display_name, u.real_name, u.handle)) if f
        ))
        return DirectoryEntry(id=u.id, kind="person", name=name, is_bot=u.is_bot, aliases=aliases)

    # ------------------------------------------------------ name -> ID lookup

    @staticmethod
    def _candidate(e: DirectoryEntry) -> dict[str, Any]:
        return {"id": e.id, "kind": e.kind, "name": e.name}

    async def resolve_channel(self, ctx: RelayContext, name: str) -> str:
        """Exact (case/diacritic-insensitive) public/private channel name -> ID."""
        target = fold(name.lstrip("#"))
        if not target:
            raise DirectoryLookupError("channel_unresolved", "A channel name is required.")

        def find(snap: _Snapshot) -> list[DirectoryEntry]:
            return [
                e for e in snap.items or []
                if e.kind in ("public", "private") and fold(e.name) == target
            ]

        try:
            matches = find(await self._conversations(ctx))
            if not matches:  # one (rate-limited) forced refresh before giving up
                matches = find(await self._conversations(ctx, force=True))
        except RelayError as e:
            raise DirectoryLookupError(
                "channel_unresolved",
                f"Could not look up channel names ({safe_error_code(e.code)}). Use a channel ID.",
            ) from None
        return self._single(matches, "channel", "a channel ID (C…/G…)")

    async def resolve_user(self, ctx: RelayContext, name: str) -> str:
        """Exact (case/diacritic-insensitive) display name, real name or handle -> user ID."""
        target = fold(name.lstrip("@"))
        if not target:
            raise DirectoryLookupError("name_not_found", "A person's name is required.")

        def find(snap: _Snapshot) -> list[DirectoryEntry]:
            return [e for e in snap.items or [] if target in e.aliases]

        try:
            matches = find(await self._people(ctx))
            if not matches:
                matches = find(await self._people(ctx, force=True))
        except RelayError as e:
            raise DirectoryLookupError(
                "name_not_found",
                f"Could not look up people ({safe_error_code(e.code)}). Use a user ID (U…) "
                "to message a person.",
            ) from None
        return self._single(matches, "person", "a user ID (U…)")

    @staticmethod
    def _single(matches: list[DirectoryEntry], what: str, id_hint: str) -> str:
        if len(matches) == 1:
            return matches[0].id
        if not matches:
            raise DirectoryLookupError(
                "name_not_found",
                f"No {what} matches that name exactly. Names are matched exactly (ignoring case "
                f"and accents), never by prefix; use find_channels to search or pass {id_hint}.",
            )
        raise DirectoryLookupError(
            "name_ambiguous",
            f"Several {what}s match that name; the candidates are listed below as untrusted "
            f"content. Retry with {id_hint}.",
            [
                {"id": e.id, "kind": e.kind, "name": e.name}
                for e in matches[:MAX_CANDIDATES]
            ],
        )

    # ------------------------------------------------------------ find_channels

    async def search(
        self,
        ctx: RelayContext,
        query: str | None,
        kinds: tuple[str, ...] | None,
        limit: int,
        cursor: str | None,
        policies: dict[str, EffectivePolicy],
    ) -> DirectoryPage:
        """Entries matching *query* (folded substring on names; exact ID), sorted, policy status
        attached, blocked ones hidden. Raises DirectoryLookupError / RelayError."""
        wanted = tuple(kinds) if kinds else DEFAULT_KINDS
        unknown = [k for k in wanted if k not in KINDS]
        if unknown:
            raise DirectoryLookupError(
                "invalid_arguments", f"'kinds' entries must be among {', '.join(KINDS)}."
            )
        needle = fold((query or "").lstrip("#@"))
        if "person" in wanted and len(needle) < MIN_PERSON_QUERY:
            raise DirectoryLookupError(
                "query_required",
                f"The 'person' kind needs a 'query' of at least {MIN_PERSON_QUERY} characters.",
            )
        offset = 0
        if cursor:
            m = _OFFSET_CURSOR_RE.match(cursor)
            if not m:
                raise DirectoryLookupError("invalid_arguments", "Invalid 'cursor'.")
            offset = int(m.group(1))
        limit = max(1, min(limit, MAX_PAGE))

        truncated = False
        pool: list[DirectoryEntry] = []
        conv_kinds = set(wanted) & {"public", "private", "dm", "group_dm"}
        conversations: list[DirectoryEntry] = []
        if conv_kinds or "person" in wanted:
            snap = await self._conversations(ctx)
            truncated |= snap.truncated
            conversations = snap.items or []
            pool += [e for e in conversations if e.kind in conv_kinds]
        people: list[DirectoryEntry] = []
        if set(wanted) & {"dm", "person"}:
            try:
                psnap = await self._people(ctx)
                truncated |= psnap.truncated
                people = psnap.items or []
            except RelayError:
                if "person" in wanted:
                    raise  # DM names are optional; the person list is not
            if "person" in wanted:
                pool += people
        by_user = {e.id: e for e in people}
        dm_by_peer = {e.peer: e.id for e in conversations if e.kind == "dm" and e.peer}

        model = ctx.spec.policy_model
        rows: list[tuple[tuple, dict[str, Any]]] = []
        order = {k: i for i, k in enumerate(KINDS)}
        for e in pool:
            name = e.name
            if e.kind == "dm":
                peer = by_user.get(e.peer or "")
                name = peer.name if peer else None
                haystacks = [fold(name)] + (list(peer.aliases) if peer else [])
            elif e.kind == "person":
                haystacks = list(e.aliases) + [fold(name)]
            else:
                haystacks = [fold(name)]
            if needle and not (
                any(needle in h for h in haystacks if h) or needle == fold(e.id)
            ):
                continue
            policy_id = e.id if e.kind != "person" else dm_by_peer.get(e.id)
            eff = policies.get(policy_id) if policy_id else None
            policy = eff.policy if eff else None
            if policy is not None and model.decide(policy, "read").kind == DENY:
                continue  # blocked conversations are hidden (and not counted)
            row: dict[str, Any] = {"id": e.id, "kind": e.kind, "name": name, "policy": policy}
            if e.kind == "dm" and e.peer:
                row["user"] = e.peer
            if e.is_bot:
                row["is_bot"] = True
            rows.append(((order[e.kind], fold(name) or "\uffff", e.id), row))
        rows.sort(key=lambda r: r[0])
        entries = [r for _, r in rows]
        page = entries[offset : offset + limit]
        nxt = f"o:{offset + limit}" if offset + limit < len(entries) else None
        return DirectoryPage(page, nxt, truncated)
