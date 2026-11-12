"""Unread monitors (issue #2103 stage 3c-ii): evaluation of ONE scheduled run.

An unread monitor follows Slack's OWN read state at run time ("unread now"): the owner's direct and
group conversations (plus explicit, already readable channels) whose newest message is newer than
the owner's ``last_read``. By default only conversations with NEW unread messages since the last
CONFIRMED delivery (``notified``) are listed, unless ``repeat``. The relay only reads: it never
marks anything read, posts or reacts. History messages carry their identity fields, so the live
echo rule is applied directly (no hydration). Pure of delivery: ``digest.py`` enqueues the result.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..adapter import ConversationInfo, RelayError
from ..slack_policy import CHANNEL_ID_RE
from . import constants as const
from .backfill import GONE_CODES
from .matcher import mentions_owner, never_content
from .normalize import Drop, event_from_message

if TYPE_CHECKING:
    from ..adapter import RelayAdapter
    from ..events import RelayEvent
    from .service import InboundService

logger = logging.getLogger(__name__)

DEFAULT_OPTIONS: dict[str, Any] = {
    "dms": True, "channels": [], "exclude": [], "mentions_only": False, "repeat": False,
}


def _tsf(ts: str | None) -> float:
    try:
        return float(ts or 0)
    except ValueError:
        return 0.0


class OptionError(Exception):
    """Unusable unread options (``invalid_filter``)."""


def parse_option_values(args: dict[str, Any]) -> tuple[bool, list[str], list[str], bool, bool]:
    """Raw tool arguments -> (dms, channel refs, exclude ids, mentions_only, repeat). ``channels``
    entries may still be names; they are resolved by the caller. Raises ``OptionError``."""
    flags = []
    for key, default in (("dms", True), ("mentions_only", False), ("repeat", False)):
        value = args.get(key)
        if value is None:
            value = default
        if not isinstance(value, bool):
            raise OptionError(f"'{key}' must be true or false.")
        flags.append(value)
    lists = []
    for key, limit in (("channels", const.UNREAD_MAX_CHANNELS), ("exclude", const.UNREAD_MAX_EXCLUDE)):
        raw = args.get(key)
        if raw is None:
            raw = []
        if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
            raise OptionError(f"'{key}' must be a list of conversation IDs" + (" or #names." if key == "channels" else "."))
        values = list(dict.fromkeys(v.strip() for v in raw))
        if len(values) > limit:
            raise OptionError(f"At most {limit} entries in '{key}'.")
        lists.append(values)
    exclude = lists[1]
    if not all(CHANNEL_ID_RE.match(c) for c in exclude):
        raise OptionError("'exclude' takes conversation IDs (C…, G…, D…).")
    return flags[0], lists[0], exclude, flags[1], flags[2]


def make_options(
    dms: bool, channels: list[str], exclude: list[str], mentions_only: bool, repeat: bool
) -> dict[str, Any]:
    """The normalized (identity-bearing) options: sorted, de-duplicated IDs."""
    return {
        "dms": dms, "channels": sorted(set(channels)), "exclude": sorted(set(exclude)),
        "mentions_only": mentions_only, "repeat": repeat,
    }


def describe_options(options: dict[str, Any], labels: dict[str, str] | None = None) -> str:
    """The options in words (tool results, ``list_monitors``)."""
    labels = labels or {}
    parts = []
    if options.get("dms"):
        n = len(options.get("exclude") or [])
        parts.append("direct and group messages" + (f" (all except {n})" if n else ""))
    channels = [labels.get(c, c) for c in options.get("channels") or []]
    if channels:
        parts.append("channels " + ", ".join(channels))
    if options.get("mentions_only"):
        parts.append("only messages that mention you")
    parts.append("every unread conversation at each run" if options.get("repeat") else "only newly unread")
    return ", ".join(parts)


@dataclass
class UnreadConversation:
    channel: str
    kind: str  # dm | group_dm | channel | private_channel
    count: int
    count_exact: bool
    newest_ts: str  # newest message listed
    consumed_ts: str  # what ``notified`` becomes once this conversation's delivery is confirmed
    last_read: str | None
    messages: list[RelayEvent] = field(default_factory=list)  # <= 5 newest, oldest first


@dataclass
class UnreadResult:
    conversations: list[UnreadConversation] = field(default_factory=list)
    omitted: int = 0  # qualifying conversations beyond the caps
    counts: Counter = field(default_factory=Counter)
    gap: bool = False
    rate_limited: bool = False
    failed: bool = False
    consumed_empty: dict[str, str] = field(default_factory=dict)  # evaluated, nothing to report


def _kind(info: ConversationInfo) -> str:
    if info.is_im:
        return "dm"
    if info.is_mpim:
        return "group_dm"
    return "private_channel" if info.is_private else "channel"


def _is_unread(info: ConversationInfo, kind: str) -> str | None:
    """None when unread, else the reason it is not (``no_unread`` | ``no_read_state``)."""
    latest, last_read = _tsf(info.latest_ts), _tsf(info.last_read)
    if kind in ("dm", "group_dm"):
        if info.unread_count is not None:
            return None if info.unread_count > 0 else "no_unread"
        if info.latest_ts is None:
            return "no_unread"
        return None if latest > last_read else "no_unread"
    if info.last_read is None:
        return "no_read_state"  # channels have no counts: without ``last_read`` nothing can be said
    if info.latest_ts is None:
        return "no_unread"
    return None if latest > last_read else "no_unread"


async def evaluate(
    svc: InboundService, adapter: RelayAdapter, monitor: dict[str, Any], policies: dict[str, Any],
) -> UnreadResult:
    """One run's candidates, newest unread first, bounded (conversations, characters, seconds).
    Rate limits stop the run (``rate_limited``); the caller decides about rescheduling."""
    res = UnreadResult()
    record = svc.record
    owner = getattr(record, "owner_user_id", None)
    self_dm = getattr(record, "self_dm_channel_id", None)
    if not owner:
        res.failed = True
        res.counts["error"] += 1
        return res
    opts = {**DEFAULT_OPTIONS, **(monitor.get("options") or {})}
    notified: dict[str, str] = monitor.get("notified") or {}
    exclude = set(opts["exclude"])
    ctx = svc._match_context()
    pace = svc.api_limiter.wait
    started = svc._mono()
    blocked = {c for c, eff in policies.items() if eff is not None and eff.policy == "blocked"}

    candidates: list[str] = []
    if opts["dms"]:
        async def list_dms() -> list[ConversationInfo]:
            await pace()
            return await adapter.list_conversations(types="im,mpim", limit=1000)

        try:
            convs = await svc.list_cache.get("im,mpim", list_dms)
        except RelayError as e:
            res.counts["rate_limited" if e.code == "ratelimited" else "list_failed"] += 1
            res.rate_limited = e.code == "ratelimited"
            res.failed = not res.rate_limited
            res.gap = True
            return res
        ids = [c.id for c in convs if c.id and c.id != self_dm and c.id not in exclude]
        for cid in ids:
            if cid in blocked:  # an explicit `blocked` entry excludes it before any call
                res.counts["blocked_channel"] += 1
            else:
                candidates.append(cid)
        if len(candidates) > const.DM_BACKFILL_MAX_CONVERSATIONS:
            res.counts["conversation_cap"] += len(candidates) - const.DM_BACKFILL_MAX_CONVERSATIONS
            res.gap = True
            candidates = candidates[: const.DM_BACKFILL_MAX_CONVERSATIONS]
    for cid in opts["channels"]:  # policy is checked BEFORE anything is fetched for a channel
        if cid == self_dm or cid in exclude or cid in candidates:
            continue
        eff = policies.get(cid)
        policy = eff.policy if eff is not None else None
        if policy == "blocked":
            res.counts["blocked_channel"] += 1
        elif policy is None:
            res.counts["policy_unset"] += 1
        else:
            candidates.append(cid)

    found: list[UnreadConversation] = []
    for index, cid in enumerate(candidates):
        if svc._mono() - started > const.UNREAD_RUN_MAX_SECONDS:
            res.counts["time_cap"] += len(candidates) - index
            res.gap = True
            break
        try:
            async def fetch_info(cid=cid) -> ConversationInfo:
                await pace()
                return await adapter.conversation_info(cid)

            info = await svc.info_cache.get(cid, fetch_info)
            kind = _kind(info)
            if info.is_member is False:
                res.counts["not_a_member"] += 1
                continue
            reason = _is_unread(info, kind)
            if reason:
                res.counts[reason] += 1
                continue
            if not opts["repeat"] and info.latest_ts and _tsf(info.latest_ts) <= _tsf(notified.get(cid)):
                res.counts["already_notified"] += 1
                continue
            await pace()
            page = await adapter.read_history(
                cid, limit=const.UNREAD_HISTORY_LIMIT, oldest=info.last_read,
            )
        except RelayError as e:
            if e.code == "ratelimited":
                res.counts["rate_limited"] += 1
                res.rate_limited = True
                res.gap = True
                return res
            if e.code in GONE_CODES:
                res.counts["conversation_gone"] += 1
                continue
            res.counts["conversation_failed"] += 1
            res.gap = True
            continue
        channel_type = "im" if kind == "dm" else "mpim" if kind == "group_dm" else "channel"
        kept: list[RelayEvent] = []
        for msg in page.messages:
            if info.last_read and _tsf(msg.ts) <= _tsf(info.last_read):
                continue
            event = event_from_message(svc.relay_id, msg, cid, channel_type=channel_type)
            if isinstance(event, Drop) or never_content(event, ctx) or event.user == owner:
                continue
            if opts["mentions_only"] and not mentions_owner(event.text, owner):
                continue
            kept.append(event)
        latest_seen = max([_tsf(info.latest_ts), *(_tsf(e.ts) for e in kept)])
        consumed = f"{latest_seen:.6f}" if latest_seen else (info.latest_ts or "0")
        if not kept:
            res.counts["no_unread"] += 1
            res.consumed_empty[cid] = consumed
            continue
        kept.sort(key=lambda e: _tsf(e.ts))
        full = len(page.messages) >= const.UNREAD_HISTORY_LIMIT or bool(page.next_cursor)
        count, exact = len(kept), not full
        if kind in ("dm", "group_dm") and info.unread_count is not None and not opts["mentions_only"]:
            if info.unread_count > count:
                count, exact = info.unread_count, False
        found.append(UnreadConversation(
            channel=cid, kind=kind, count=count, count_exact=exact, newest_ts=kept[-1].ts,
            consumed_ts=consumed, last_read=info.last_read,
            messages=kept[-const.UNREAD_MESSAGES_PER_CONVERSATION:],
        ))

    found.sort(key=lambda c: _tsf(c.newest_ts), reverse=True)
    chars = 0
    for conv in found:
        size = sum(min(len(m.text), const.UNREAD_MESSAGE_CHARS + 40) for m in conv.messages)
        if res.conversations and (
            len(res.conversations) >= const.UNREAD_MAX_CONVERSATIONS or chars + size > const.ENVELOPE_MAX_CHARS
        ):
            res.omitted += 1
            continue
        res.conversations.append(conv)
        chars += size
    return res
