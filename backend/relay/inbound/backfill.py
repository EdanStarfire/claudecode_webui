"""Backfill strategies of the scope monitors (issue #2103 stage 3b-ii).

``InboundService.backfill_monitors`` dispatches here for ``monitor_dms`` / ``monitor_mentions``.
Every strategy feeds ``coalescer.add(event, [monitor])`` (per-monitor key dedupe, flood limits,
cursor-on-confirm) and matches ONLY the monitor being filled. Counts of what was dropped and why
(``reason_counts``) are audited, never any message text.

- DMs / MPIMs: one ``users.conversations`` call shared by all DM monitors, then per conversation
  (serialized, capped) the newest ts from ``conversations.info`` decides whether a history fetch is needed.
- Mentions: ``search.messages "<@OWNER>"`` newest first (no date filters: Slack's search index lags,
  so the window is re-read from ``cursor - 60 s``), then EVERY hit is hydrated (``hydrate.py``)
  and judged by the live echo rule before it may be delivered. Nothing is delivered unhydrated.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..adapter import RelayError
from . import constants as const
from .hydrate import HydrationRateLimitedError, hydrate
from .matcher import matches_monitor, never_content
from .normalize import Drop, event_from_message

if TYPE_CHECKING:
    from ..adapter import RelayAdapter
    from .service import InboundService

logger = logging.getLogger(__name__)

# Slack errors of a conversation that can no longer be read at all (not a transient gap)
GONE_CODES = frozenset({"channel_not_found", "user_disabled", "is_archived"})


def _tsf(ts: str | None) -> float:
    try:
        return float(ts or 0)
    except ValueError:
        return 0.0


@dataclass
class BackfillRun:
    """State of one backfill run: hydration budget / cache, drop counts, the gap flag."""

    now: float
    hydrations: int = 0
    cache: dict[str, Any] = field(default_factory=dict)  # "channel:ts" -> hydrated message | None
    counts: Counter = field(default_factory=Counter)
    gap: bool = False
    gap_from: float | None = None
    # --- digest runs (3c-i): collect instead of buffering, own window / hydration cap, paced calls
    digest: bool = False
    sink: Callable[[Any, dict[str, Any], bool], None] | None = None
    hydration_cap: int = const.HYDRATION_CAP
    max_age: float = const.MONITOR_BACKFILL_MAX_AGE_SECONDS
    pacer: Callable[[], Awaitable[None]] | None = None

    async def pace(self) -> None:
        """Digest runs space their Tier-3 calls (shared limiter); live backfills do not."""
        if self.pacer is not None:
            await self.pacer()

    def note_gap(self, since: float) -> None:
        self.gap = True
        self.gap_from = since if self.gap_from is None else min(self.gap_from, since)


def monitor_cutoff(svc: InboundService, monitor: dict[str, Any], run: BackfillRun) -> float:
    """Messages at or before this ts are already covered (cursor / high water minus the margin,
    never older than the 24 h cap)."""
    mid = monitor["id"]
    durable = monitor.get("last_seen_ts") or f"{run.now:.6f}"
    # a digest re-reads from its durable cursor only: nothing is buffered, so no high water exists
    high = durable if run.digest else (svc._high_water.get(mid) or durable)
    floor = run.now - run.max_age
    return max(_tsf(high), floor) - const.BACKFILL_MARGIN_SECONDS


def _older_omitted(monitor: dict[str, Any], run: BackfillRun) -> bool:
    durable = monitor.get("last_seen_ts") or f"{run.now:.6f}"
    return _tsf(durable) < run.now - run.max_age


def _deliver(
    svc: InboundService, event, monitors: list[dict[str, Any]], run: BackfillRun, ctx,
    owner: str | None,
) -> None:
    """Judge a (hydrated / fetched) event and buffer it for every monitor it matches."""
    reason = never_content(event, ctx)
    if reason:
        run.counts[reason] += 1
        return
    for m in monitors:
        if _tsf(event.ts) <= monitor_cutoff(svc, m, run):
            continue
        if matches_monitor(event, m, owner):
            if run.sink is not None:
                run.sink(event, m, _older_omitted(m, run))
            else:
                svc.coalescer.add(event, [m], older_omitted=_older_omitted(m, run))


# ------------------------------------------------------------------------------ DMs / MPIMs


async def backfill_dms(
    svc: InboundService, adapter: RelayAdapter, monitors: list[dict[str, Any]], run: BackfillRun
) -> None:
    record = svc.record
    if record is None or not record.owner_user_id:
        return
    owner, self_dm = record.owner_user_id, record.self_dm_channel_id
    ctx = svc._match_context()
    try:
        await run.pace()
        conversations = await adapter.list_conversations(types="im,mpim", limit=1000)
    except RelayError as e:
        run.counts["rate_limited" if e.code == "ratelimited" else "list_failed"] += 1
        run.note_gap(min(monitor_cutoff(svc, m, run) for m in monitors))
        return
    conversations = [c for c in conversations if c.id and c.id != self_dm]
    if len(conversations) > const.DM_BACKFILL_MAX_CONVERSATIONS:
        run.counts["conversation_cap"] += len(conversations) - const.DM_BACKFILL_MAX_CONVERSATIONS
        run.note_gap(min(monitor_cutoff(svc, m, run) for m in monitors))
        conversations = conversations[: const.DM_BACKFILL_MAX_CONVERSATIONS]
    cutoffs = {m["id"]: monitor_cutoff(svc, m, run) for m in monitors}
    oldest = min(cutoffs.values())
    for conv in conversations:  # serialized: one Slack call at a time
        try:
            await run.pace()
            info = await adapter.conversation_info(conv.id)
            channel_type = "mpim" if (info.is_mpim or conv.is_mpim) else "im"
            if info.latest_ts is not None and _tsf(info.latest_ts) <= oldest:
                continue  # nothing newer than every cursor: no history call needed
            page_cursor: str | None = None
            for page_no in range(const.BACKFILL_MAX_PAGES):
                await run.pace()
                page = await adapter.read_history(
                    conv.id, limit=const.BACKFILL_PAGE_SIZE, oldest=f"{oldest:.6f}", cursor=page_cursor
                )
                for msg in sorted(page.messages, key=lambda x: _tsf(x.ts)):
                    event = event_from_message(svc.relay_id, msg, conv.id, channel_type=channel_type)
                    if isinstance(event, Drop):
                        continue
                    _deliver(svc, event, monitors, run, ctx, owner)
                page_cursor = page.next_cursor
                if not page_cursor:
                    break
                if page_no == const.BACKFILL_MAX_PAGES - 1:
                    run.counts["truncated"] += 1
                    run.note_gap(oldest)
        except RelayError as e:
            if e.code == "ratelimited":
                run.counts["rate_limited"] += 1
                run.note_gap(oldest)
                return  # a rate limit stops the run; the gap notice tells the session
            if e.code in GONE_CODES:
                # a DM with a deactivated / removed user (seen live: ``channel_not_found``): nothing
                # deliverable exists there, so it is skipped without a (permanent) gap notice
                run.counts["conversation_gone"] += 1
                continue
            run.counts["conversation_failed"] += 1
            run.note_gap(oldest)


# ------------------------------------------------------------------------------ mentions


async def backfill_mentions(
    svc: InboundService, adapter: RelayAdapter, monitors: list[dict[str, Any]], run: BackfillRun
) -> None:
    record = svc.record
    if record is None or not record.owner_user_id:
        return
    owner, self_dm = record.owner_user_id, record.self_dm_channel_id
    ctx = svc._match_context()
    cutoffs = {m["id"]: monitor_cutoff(svc, m, run) for m in monitors}
    stop_at = min(cutoffs.values())
    try:
        await run.pace()
        member_of = {
            c.id for c in await adapter.list_conversations(
                types="public_channel,private_channel,mpim,im", limit=5000
            )
        }
    except RelayError as e:
        # without the membership set no hit can be vetted: fail closed
        run.counts["rate_limited" if e.code == "ratelimited" else "list_failed"] += 1
        run.note_gap(stop_at)
        return
    query = f"<@{owner}>"
    done = False
    for page_no in range(1, const.SEARCH_MAX_PAGES + 1):
        await svc.search_limiter.wait()
        try:
            page = await adapter.search_page(query, page=page_no, count=100, sort="timestamp", sort_dir="desc")
        except RelayError as e:
            run.counts["rate_limited" if e.code == "ratelimited" else "search_failed"] += 1
            run.note_gap(stop_at)
            return
        for hit in page.hits:  # newest first
            msg = hit.message
            channel, ts = msg.channel, msg.ts
            if not channel or not ts:
                continue
            if _tsf(ts) <= stop_at:
                done = True  # everything after this is older than every cursor
                break
            if channel == self_dm or msg.user == owner:
                continue
            if channel not in member_of:
                run.counts["non_member"] += 1
                continue
            key = f"{channel}:{ts}"
            needing = [
                m for m in monitors
                if _tsf(ts) > cutoffs[m["id"]] and not svc.monitor_key_seen(m["id"], key)
            ]
            if not needing:
                continue  # already known: costs no call
            if key not in run.cache:
                if run.hydrations >= run.hydration_cap:
                    run.counts["hydration_cap"] += 1
                    run.note_gap(stop_at)
                    continue
                run.hydrations += 1
                try:
                    await run.pace()
                    run.cache[key] = await hydrate(adapter, channel, ts, msg.permalink)
                except HydrationRateLimitedError:
                    run.counts["rate_limited"] += 1
                    run.note_gap(stop_at)
                    return
            hydrated = run.cache[key]
            if hydrated is None:
                run.counts["hydration_failed"] += 1
                run.note_gap(stop_at)  # a hit that cannot be vetted may be lost once the cursor passes it
                continue
            channel_type = "mpim" if hit.is_mpim else "im" if hit.is_im else "channel"
            event = event_from_message(svc.relay_id, hydrated, channel, channel_type=channel_type)
            if isinstance(event, Drop):
                continue
            _deliver(svc, event, needing, run, ctx, owner)
        if done:
            break
        if page_no >= page.pages:
            done = True
            break
    if not done:
        run.counts["truncated"] += 1  # the search pages ran out before the cursor was reached
        run.note_gap(stop_at)


# ------------------------------------------------------------- thread / channel (digest runs)


async def collect_thread_or_channel(
    svc: InboundService, adapter: RelayAdapter, monitor: dict[str, Any], run: BackfillRun
) -> None:
    """A thread / channel digest's window: replies of the thread (or top-level history of the
    channel) since the cursor, judged exactly like live events, handed to ``run.sink``."""
    cutoff = monitor_cutoff(svc, monitor, run)
    ctx = svc._match_context()
    is_thread = monitor["kind"] == const.KIND_MONITOR_THREAD
    page_cursor: str | None = None
    messages = []
    try:
        for page_no in range(const.BACKFILL_MAX_PAGES):
            await run.pace()
            if is_thread:
                page = await adapter.read_thread(
                    monitor["channel"], monitor["thread_ts"], const.BACKFILL_PAGE_SIZE, page_cursor,
                    oldest=f"{cutoff:.6f}",
                )
            else:
                page = await adapter.read_history(
                    monitor["channel"], limit=const.BACKFILL_PAGE_SIZE, oldest=f"{cutoff:.6f}",
                    cursor=page_cursor,
                )
            messages.extend(page.messages)
            page_cursor = page.next_cursor
            if not page_cursor:
                break
            if page_no == const.BACKFILL_MAX_PAGES - 1:
                run.counts["truncated"] += 1
                run.note_gap(cutoff)
    except RelayError as e:
        run.counts["rate_limited" if e.code == "ratelimited" else "fetch_failed"] += 1
        run.note_gap(cutoff)
        return
    owner = ctx.owner_user_id
    for msg in sorted(messages, key=lambda x: _tsf(x.ts)):
        if _tsf(msg.ts) <= cutoff:
            continue
        event = event_from_message(svc.relay_id, msg, monitor["channel"], channel_type=None)
        if isinstance(event, Drop):
            continue
        if event.thread_ts is None and is_thread:
            event.thread_ts = monitor["thread_ts"]
        _deliver(svc, event, [monitor], run, ctx, owner)
