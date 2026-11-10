"""Per-session coalescer for monitored content (issue #2103 stage 3b-i).

Matched messages are buffered in memory per monitoring session; the first message of an empty
buffer opens a 60 s window, a single flush task per session sleeps until it fires and then builds
ONE envelope. Ingress is flood-limited (per monitor and per relay); a full buffer turns further
messages into a "+N more" tally; a session that already has a pending monitor envelope, or has
received 12 in the last hour, has the flush postponed in 60 s steps up to 10 minutes.

Nothing here is persisted: a lost buffer is covered by the durable per-monitor cursor and the
reconnect backfill (cursor-on-confirm, see ``InboundService``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..events import RelayEvent
from . import constants as const
from .flood import KeyedLimiters, RollingLimiter, SessionEnvelopeLimiter

if TYPE_CHECKING:
    from .service import InboundService

logger = logging.getLogger(__name__)


def _tsf(ts: str | None) -> float:
    try:
        return float(ts or 0)
    except ValueError:
        return 0.0


@dataclass
class BufferedMessage:
    event: RelayEvent
    monitor_ids: list[str]


@dataclass
class SessionBuffer:
    session_id: str
    opened_at: float
    messages: dict[str, BufferedMessage] = field(default_factory=dict)  # "channel:ts" -> message
    chars: int = 0
    cover: dict[str, str] = field(default_factory=dict)  # monitor id -> newest ts consumed
    overflow: Counter = field(default_factory=Counter)  # channel -> messages beyond the buffer caps
    flooded: set[str] = field(default_factory=set)  # monitors that dropped a message this window
    older_omitted: bool = False
    task: asyncio.Task | None = None


class Coalescer:
    def __init__(
        self, svc: InboundService, *, now: Callable[[], float], mono: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        window: float = const.MONITOR_WINDOW_SECONDS,
    ) -> None:
        self._svc = svc
        self._now = now
        self._sleep = sleep
        self._window = window
        self._buffers: dict[str, SessionBuffer] = {}
        self._monitor_limits = KeyedLimiters(const.FLOOD_PER_MONITOR, const.FLOOD_WINDOW_SECONDS, mono)
        self._relay_limit = RollingLimiter(const.FLOOD_PER_RELAY, const.FLOOD_WINDOW_SECONDS, mono)
        self.envelopes = SessionEnvelopeLimiter(const.MAX_ENVELOPES_PER_HOUR, 3600.0, mono)

    # ---------------------------------------------------------------- ingress

    def add(self, event: RelayEvent, monitors: list[dict], *, older_omitted: bool = False) -> None:
        """Buffer *event* for its matched monitors (grouped per session). Never blocks."""
        by_session: dict[str, list[dict]] = {}
        for m in monitors:
            by_session.setdefault(m["session_id"], []).append(m)
        relay_ok = self._relay_limit.allow()
        for session_id, session_monitors in by_session.items():
            self._add_for_session(session_id, event, session_monitors, relay_ok, older_omitted)

    def _add_for_session(
        self, session_id: str, event: RelayEvent, monitors: list[dict], relay_ok: bool,
        older_omitted: bool,
    ) -> None:
        buf = self._buffers.get(session_id)
        if buf is None:
            buf = SessionBuffer(session_id, self._now())
            self._buffers[session_id] = buf
        key = f"{event.channel}:{event.ts}"
        admitted: list[str] = []
        for m in monitors:
            mid = m["id"]
            self._svc.note_high_water(mid, event.ts)
            existing = buf.messages.get(key)
            if existing is not None and mid in existing.monitor_ids:
                continue  # the same (monitor, message) pair twice: one delivery
            if not relay_ok or not self._monitor_limits.allow(mid):
                self._drop_flooded(buf, m, event)
                continue
            admitted.append(mid)
        if older_omitted:
            buf.older_omitted = True
        if admitted:
            self._admit(buf, key, event, admitted)
        if buf.messages or buf.overflow or buf.cover:
            self._ensure_task(buf)

    def _drop_flooded(self, buf: SessionBuffer, monitor: dict, event: RelayEvent) -> None:
        mid = monitor["id"]
        self._cover(buf, mid, event.ts)
        self._svc.store.bump_counters(mid, dropped=1)
        if mid not in buf.flooded:
            buf.flooded.add(mid)
            self._svc.spawn_audit(
                event="monitor_flood", outcome="dropped", code="flood_limit", monitor_id=mid,
                monitor_kind=monitor.get("kind"), session_id=monitor["session_id"],
                channel_id=monitor.get("channel"),
            )

    def _admit(self, buf: SessionBuffer, key: str, event: RelayEvent, monitor_ids: list[str]) -> None:
        existing = buf.messages.get(key)
        if existing is not None:
            for mid in monitor_ids:
                if mid not in existing.monitor_ids:
                    existing.monitor_ids.append(mid)
                    self._svc.store.bump_counters(mid, coalesced=1)
            return
        size = min(len(event.text), const.MESSAGE_MAX_CHARS + 40)
        if (
            len(buf.messages) >= const.ENVELOPE_MAX_MESSAGES
            or buf.chars + size > const.ENVELOPE_MAX_CHARS
        ) and buf.messages:
            buf.overflow[event.channel] += 1  # tallied, not buffered: memory stays bounded
            for mid in monitor_ids:
                self._cover(buf, mid, event.ts)
            return
        buf.messages[key] = BufferedMessage(event, list(monitor_ids))
        buf.chars += size

    @staticmethod
    def _cover(buf: SessionBuffer, monitor_id: str, ts: str) -> None:
        if _tsf(ts) > _tsf(buf.cover.get(monitor_id)):
            buf.cover[monitor_id] = ts

    def _ensure_task(self, buf: SessionBuffer) -> None:
        if buf.task is None or buf.task.done():
            try:
                buf.task = asyncio.get_running_loop().create_task(
                    self._run(buf), name=f"inbound-coalesce-{buf.session_id}"
                )
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ flush

    async def _run(self, buf: SessionBuffer) -> None:
        try:
            await self._sleep(self._window)
            waited = 0.0
            svc = self._svc
            while waited < const.POSTPONE_MAX_SECONDS and (
                svc.port.has_pending_monitor(buf.session_id, svc.relay_id)
                or not self.envelopes.allowed(buf.session_id)
            ):
                await self._sleep(const.POSTPONE_STEP_SECONDS)
                waited += const.POSTPONE_STEP_SECONDS
            if self._buffers.get(buf.session_id) is buf:
                del self._buffers[buf.session_id]  # later messages open a new window
            await svc.flush_buffer(buf)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitor flush failed")
            if self._buffers.get(buf.session_id) is buf:
                del self._buffers[buf.session_id]

    # --------------------------------------------------------------- lifecycle

    def discard_session(self, session_id: str) -> None:
        buf = self._buffers.pop(session_id, None)
        if buf is not None and buf.task is not None:
            buf.task.cancel()
        self.envelopes.discard(session_id)

    def discard_monitor(self, monitor_id: str) -> None:
        """Remove a removed / suspended monitor's pending messages (flush re-checks anyway)."""
        self._monitor_limits.discard(monitor_id)

    async def stop(self) -> None:
        """Graceful stop: buffers are discarded WITHOUT enqueue; durable cursors stay so the next
        start re-fetches whatever was buffered."""
        bufs = list(self._buffers.values())
        self._buffers.clear()
        tasks = [b.task for b in bufs if b.task is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def buffered(self, session_id: str) -> SessionBuffer | None:
        return self._buffers.get(session_id)
