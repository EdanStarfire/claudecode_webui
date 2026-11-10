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

    def note_gap(self, since: float) -> None:
        self.gap = True
        self.gap_from = since if self.gap_from is None else min(self.gap_from, since)


def monitor_cutoff(svc: InboundService, monitor: dict[str, Any], run: BackfillRun) -> float:
    """Messages at or before this ts are already covered (cursor / high water minus the margin,
    never older than the 24 h cap)."""
    mid = monitor["id"]
    durable = monitor.get("last_seen_ts") or f"{run.now:.6f}"
    high = svc._high_water.get(mid) or durable
    floor = run.now - const.MONITOR_BACKFILL_MAX_AGE_SECONDS
    return max(_tsf(high), floor) - const.BACKFILL_MARGIN_SECONDS


def _older_omitted(monitor: dict[str, Any], run: BackfillRun) -> bool:
    durable = monitor.get("last_seen_ts") or f"{run.now:.6f}"
    return _tsf(durable) < run.now - const.MONITOR_BACKFILL_MAX_AGE_SECONDS


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
            info = await adapter.conversation_info(conv.id)
            channel_type = "mpim" if (info.is_mpim or conv.is_mpim) else "im"
            if info.latest_ts is not None and _tsf(info.latest_ts) <= oldest:
                continue  # nothing newer than every cursor: no history call needed
            page_cursor: str | None = None
            for page_no in range(const.BACKFILL_MAX_PAGES):
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
                if run.hydrations >= const.HYDRATION_CAP:
                    run.counts["hydration_cap"] += 1
                    run.note_gap(stop_at)
                    continue
                run.hydrations += 1
                try:
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
