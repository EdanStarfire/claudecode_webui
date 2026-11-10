"""Hydration of search hits (issue #2103 stage 3b-ii).

A ``search.messages`` hit carries no app / bot identity and no metadata, so a message the session
itself posted (which mentions the owner) is indistinguishable from a human's. Before a hit may be
delivered it is re-fetched as a single message with ``include_all_metadata`` and judged by the same
live echo rule as a live event. Everything here FAILS CLOSED: any error, rate limit or missing message
returns None and the hit is dropped (never delivered unhydrated).
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from ..adapter import RelayAdapter, RelayError, RelayMessage

logger = logging.getLogger(__name__)


class HydrationRateLimitedError(Exception):
    """Slack rate limited the fetch: the run stops hydrating."""


def thread_ts_from_permalink(permalink: str | None) -> str | None:
    """``?thread_ts=`` of a hit's permalink (a thread reply), or None."""
    if not permalink:
        return None
    try:
        values = parse_qs(urlparse(permalink).query).get("thread_ts")
    except ValueError:
        return None
    return values[0] if values and values[0] else None


async def hydrate(
    adapter: RelayAdapter, channel: str, ts: str, permalink: str | None
) -> RelayMessage | None:
    """The message at (channel, ts) as Slack returns it now, or None (fail closed)."""
    try:
        page = await adapter.read_history(channel, limit=1, oldest=ts, latest=ts, inclusive=True)
        for msg in page.messages:
            if msg.ts == ts:
                return msg
        thread_ts = thread_ts_from_permalink(permalink)
        if thread_ts and thread_ts != ts:
            # a thread reply is not in the channel history: fetch it through its thread (the
            # parent is always returned first, so ask for two and pick the entry that is the hit)
            page = await adapter.read_thread(
                channel, thread_ts, limit=2, oldest=ts, latest=ts, inclusive=True
            )
            for msg in page.messages:
                if msg.ts == ts:
                    return msg
    except RelayError as e:
        if e.code == "ratelimited":
            raise HydrationRateLimitedError from None
        logger.debug("Hit hydration failed (%s)", e.code)
    except Exception:
        logger.debug("Hit hydration failed", exc_info=True)
    return None
