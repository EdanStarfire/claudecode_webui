"""InboundService: one per relay; the Socket Mode inbound pipeline (issue #2103 stage 3a).

Slack envelope -> normalize -> dedupe -> classify (pure trust rule) -> persisted in-flight entry ->
ack -> single worker -> age window / rate limit / session checks -> delivery sink -> audit.

Ack semantics are at-most-once with a persisted in-flight list: an accepted instruction is written
(fsync) before the handler returns (= before the ack). Events accepted but not yet enqueued are
re-run at startup. Once enqueued into the session's pending queue an instruction counts as
delivered from the relay's point of view; the pending queue itself is not recovered across a
Backend restart (pre-existing, issue #2362).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.session_lifecycle import is_errored

from ..adapter import RelayError
from ..audit import text_fields
from ..events import RelayEvent
from ..safety import safe_error_code
from . import constants as const
from . import content as monitor_content
from .coalescer import BufferedMessage, Coalescer, SessionBuffer
from .dispatch import format_instruction, instruction_summary
from .matcher import MatchContext, matches_monitor, never_content
from .matcher import match as match_monitors
from .normalize import Drop, event_from_message, normalize_event
from .ratelimit import KeyedBuckets, TokenBucket
from .responder import InboundResponder
from .router import KIND_IGNORE, KIND_INSTRUCTION, KIND_REPLY, classify
from .state import InboundState, ts_key
from .store import ACTIVE, SUSPENDED, JsonSubscriptionStore
from .transport import (
    RawEnvelope,
    SocketTransport,
    TransportAuthError,
    TransportUnavailableError,
)
from .watcher import STATUS_GONE, DeliveryWatcher, WatchEntry

if TYPE_CHECKING:
    from backend.relay.audit import RelayAuditLog
    from backend.relay.service import RelayService
    from backend.relay_manager import RelayManager
    from backend.session_manager import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class DeliveryOutcome:
    status: str  # delivered | already_delivered | failed
    error: str | None = None


InstructionSink = Callable[..., Awaitable[DeliveryOutcome]]
TransportFactory = Callable[[str], SocketTransport]


def comm_id_for(relay_id: str, channel: str, ts: str) -> str:
    """Deterministic: the same Slack message always maps to the same comm."""
    return str(uuid.uuid5(const.COMM_NAMESPACE, f"{relay_id}:{channel}:{ts}"))


class _NullPort:
    """No coordinator bound (tests of the pure pipeline): monitor envelopes cannot be delivered."""

    async def enqueue(self, session_id: str, **kwargs) -> DeliveryOutcome:
        return DeliveryOutcome("failed", "no delivery port")

    def item_status(self, session_id: str, comm_id: str) -> str | None:
        return None

    def pending_monitor_count(self, session_id: str, relay_id: str) -> int:
        return 0

    async def cancel(self, session_id: str, comm_id: str) -> bool:
        return False


def monitor_comm_id(relay_id: str, session_id: str, keys: list[str], attempt: int = 0) -> str:
    """Deterministic over the envelope's message keys: re-flushing the same buffer while the first
    attempt is still queued is the same comm (the queue dedupes it). A new ``attempt`` is used when
    the earlier comm already reached a terminal status (failed / cancelled / delivered), so a retry
    is never swallowed by the old ledger item."""
    digest = hashlib.sha256("|".join(keys).encode()).hexdigest()
    suffix = f":{attempt}" if attempt else ""
    return str(uuid.uuid5(const.MONITOR_COMM_NAMESPACE, f"{relay_id}:{session_id}:{digest}{suffix}"))


class InboundService:
    def __init__(
        self,
        relay_id: str,
        *,
        relay_manager: RelayManager,
        relay_service: RelayService,
        session_manager: SessionManager,
        audit: RelayAuditLog,
        sink: InstructionSink,
        relays_root: Path,
        transport_factory: TransportFactory,
        now: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        port: Any = None,
        names: Any = None,
        window_sleep: Callable[[float], Awaitable[None]] | None = None,
        watch_sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.relay_id = relay_id
        self._manager = relay_manager
        self._relay_service = relay_service
        self._sessions = session_manager
        self._audit = audit
        self._sink = sink
        self._factory = transport_factory
        self._now, self._mono, self._sleep, self._rng = now, mono, sleep, rng
        relay_dir = relays_root / relay_id
        self.state = InboundState(relay_dir)
        self.store = JsonSubscriptionStore(relay_dir, relay_id, clock=now)
        self._responder = InboundResponder(relay_manager)
        self._relay_bucket = TokenBucket(const.RELAY_RATE_PER_MINUTE, const.RELAY_BURST, mono)
        self._thread_buckets = KeyedBuckets(const.THREAD_RATE_PER_MINUTE, const.THREAD_BURST, mono)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._backfill_task: asyncio.Task | None = None
        self._transport: SocketTransport | None = None
        self._stop = asyncio.Event()
        self._stopping = False
        self._drops: Counter[str] = Counter()
        self._ack_tasks: dict[str, asyncio.Task] = {}  # in-flight key -> reaction add (issue #2364)
        self._rate_audit_at = -1e9
        self.status = const.STATUS_STOPPED
        self.detail: str | None = None
        self.connected_at: float | None = None
        # --- monitors (stage 3b-i)
        self.port = port or _NullPort()
        self._names = names
        self.coalescer = Coalescer(
            self, now=now, mono=mono, sleep=window_sleep or asyncio.sleep,
        )
        self.watcher = DeliveryWatcher(
            self.port, self._on_watch_outcome, now=now, sleep=watch_sleep or asyncio.sleep,
        )
        self._high_water: dict[str, str] = {}  # monitor id -> newest ts buffered (in memory)
        self._hold: set[str] = set()  # monitors whose durable cursor is frozen after a failed envelope
        self._recent_keys: dict[str, OrderedDict[str, None]] = {}  # monitor id -> recent message keys
        self._policy_lock = asyncio.Lock()  # one policy evaluation at a time (suspend/resume do not interleave)
        self._replied: set[str] = set()  # instruction comm ids that already got a failure reply
        self._flush_audits: set[asyncio.Task] = set()

    # ------------------------------------------------------------- lifecycle

    @property
    def record(self):
        return self._manager.relays.get(self.relay_id)

    async def start(self) -> None:
        self._stopping = False
        self._stop.clear()
        self.state.load()
        self.store.load()
        self._high_water = {
            m["id"]: m["last_seen_ts"] for m in self.store.monitors() if m.get("last_seen_ts")
        }  # restart: high water = durable cursor, so enqueued-but-lost content is re-fetched
        self.watcher.start()
        if not self.state.existed:
            self.state.initialize_first_start(self._now())
        record = self.record
        if self.state.was_disabled and record is not None and record.inbound_enabled:
            # messages typed while the owner had inbound off are never replayed on re-enable
            if record.self_dm_channel_id:
                self.state.cursors[record.self_dm_channel_id] = f"{self._now():.6f}"
            self.state.was_disabled = False
        if record is not None and record.self_dm_channel_id:
            # first start: nothing before enablement is ever backfilled
            self.state.cursors.setdefault(record.self_dm_channel_id, f"{self._now():.6f}")
        await self.state.flush()
        for key in list(self.state.inflight):
            self._queue.put_nowait(key)  # re-run accepted-but-unfinished instructions
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._worker(), name=f"inbound-worker-{self.relay_id}"),
            loop.create_task(self._supervise(), name=f"inbound-supervisor-{self.relay_id}"),
            loop.create_task(self._housekeeping_loop(), name=f"inbound-housekeeping-{self.relay_id}"),
        ]
        if self.store.monitors():  # policies / attachments may have changed while the Backend was down
            self._spawn(self.reevaluate_all())

    async def stop(self) -> None:
        self._stopping = True
        self._stop.set()
        tasks = list(self._tasks)
        if self._backfill_task is not None:
            tasks.append(self._backfill_task)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.coalescer.stop()  # buffers are dropped without enqueue; cursors stay durable
        await self.watcher.stop()
        self._tasks = []
        self._backfill_task = None
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                await transport.disconnect()
            except Exception:
                logger.debug("Inbound transport disconnect failed", exc_info=True)
        await self._flush_drop_summary()
        await self.store.close()
        await self.state.close()
        self._set_status(const.STATUS_STOPPED, None, audit=False)

    # ---------------------------------------------------------------- status

    def _set_status(self, status: str, detail: str | None, *, audit: bool = True) -> None:
        changed = status != self.status or detail != self.detail
        self.status, self.detail = status, detail
        if status == const.STATUS_CONNECTED:
            if self.connected_at is None:
                self.connected_at = self._now()
        else:
            self.connected_at = None
        if changed and audit and not self._stopping:
            self._spawn(self._audit.record(
                self.relay_id, event="inbound_connection", inbound_status=status, code=detail,
            ))

    def _spawn(self, coro) -> asyncio.Task | None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return None
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)
        return task

    def status_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "detail": self.detail, "connected_at": self.connected_at,
            "inflight": len(self.state.inflight),
        }

    async def _eligibility(self) -> str | None:
        """None when the service may connect, else a sanitised reason (status ``disabled``)."""
        record = self.record
        if record is None:
            return "relay_missing"
        if not record.inbound_enabled:
            return "disabled_by_owner"
        if not record.app_token_secret or not self._manager.has_token(self.relay_id, "app"):
            return "no_app_token"
        if not self._manager.has_token(self.relay_id, "user"):
            return "no_user_token"
        if not record.owner_user_id:
            return "no_owner"
        if not record.self_dm_channel_id:
            try:
                dm = await self._manager.adapter_for(self.relay_id).open_dm(record.owner_user_id)
            except RelayError:
                return "no_self_dm"
            if not dm:
                return "no_self_dm"
            self._manager.record_self_dm(self.relay_id, record.owner_user_id, dm)
            self.state.cursors.setdefault(dm, f"{self._now():.6f}")
        return None

    # ------------------------------------------------------------ supervisor

    async def _supervise(self) -> None:
        reason = await self._eligibility()
        if reason:
            if reason == "disabled_by_owner":
                self.state.was_disabled = True  # the next enabled start skips what was typed meanwhile
                await self.state.flush()
            self._set_status(const.STATUS_DISABLED, reason)
            await self._stop.wait()
            return
        backoff = const.BACKOFF_BASE_SECONDS
        last_attempt = -1e9
        while not self._stopping:
            wait = const.MIN_CONNECT_SPACING_SECONDS - (self._mono() - last_attempt)
            if wait > 0:
                await self._sleep(wait)
            last_attempt = self._mono()
            self._set_status(const.STATUS_CONNECTING, None)
            closed = asyncio.Event()
            auth_code: list[str] = []

            def on_state(state: str, closed=closed, auth_code=auth_code) -> None:
                self._on_transport_state(state, closed, auth_code)

            transport = self._factory(self.relay_id)
            transport.set_handlers(self.on_envelope, on_state)
            self._transport = transport
            connected_at: float | None = None
            try:
                await transport.connect()
                connected_at = self._mono()
                if self.status != const.STATUS_CONNECTED:
                    self._set_status(const.STATUS_CONNECTED, None)
                waiter = asyncio.ensure_future(closed.wait())
                stopper = asyncio.ensure_future(self._stop.wait())
                await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                stopper.cancel()
            except TransportAuthError as e:
                auth_code.append(e.code)
            except TransportUnavailableError as e:
                if e.code == "slack_sdk_missing":
                    self._set_status(const.STATUS_TRANSPORT_UNAVAILABLE, e.code)
                    await self._stop.wait()
                    return
                self._set_status(const.STATUS_BACKOFF, safe_error_code(e.code))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Inbound transport error")
                self._set_status(const.STATUS_BACKOFF, "transport_error")
            try:
                await transport.disconnect()
            except Exception:
                logger.debug("Inbound transport disconnect failed", exc_info=True)
            self._transport = None
            if self._stopping:
                return
            if auth_code:
                self._set_status(const.STATUS_AUTH_FAILED, safe_error_code(auth_code[0]))
                await self._stop.wait()  # no retries until credentials change (service restarted)
                return
            if connected_at is not None and self._mono() - connected_at >= const.STABLE_CONNECTION_SECONDS:
                backoff = const.BACKOFF_BASE_SECONDS
            if self.status != const.STATUS_BACKOFF:
                self._set_status(const.STATUS_BACKOFF, self.detail)
            delay = min(backoff, const.BACKOFF_MAX_SECONDS) * (0.5 + 0.5 * self._rng())
            await self._sleep(delay)
            backoff = min(backoff * 2, const.BACKOFF_MAX_SECONDS)

    def _on_transport_state(self, state: str, closed: asyncio.Event, auth_code: list[str]) -> None:
        if state == "connected":
            self._set_status(const.STATUS_CONNECTED, None)
        elif state == "reconnecting":
            self._set_status(const.STATUS_CONNECTING, None)
        elif state == "auth_failed":
            auth_code.append("invalid_auth")
            closed.set()
        elif state == "dropped":
            closed.set()

    # -------------------------------------------------------------- envelopes

    async def on_envelope(self, env: RawEnvelope) -> None:
        """Transport handler; its return is the ack point. Local work only (no Slack, no session)."""
        if env.type == "hello":
            self._start_backfill()
            return
        if env.type != "events_api":
            return
        record = self.record
        result = normalize_event(self.relay_id, env.payload, record.workspace_id if record else None)
        if isinstance(result, Drop):
            self._drop(result.reason)
            return
        await self.accept_event(result)

    def _drop(self, reason: str) -> None:
        self._drops[reason] += 1

    async def accept_event(self, event: RelayEvent) -> bool:
        """Dedupe + classify + persist-before-ack. True when queued as an instruction/refusal.

        Ring discipline (stage 3b-i, M2): user-token events arrive for EVERY message of every channel
        the owner is in, so the dedupe ring is marked only for instruction candidates (below) and for
        events that match a monitor. An unmatched event is never remembered, so a flood of unrelated
        traffic cannot evict instruction keys (a replayed old instruction would otherwise get a
        second "received late" reply); its twin is classified again and dropped again, harmlessly."""
        if self.state.seen(event.channel, event.ts):
            self._drop("deduped")
            return False
        self._learn_app_id(event)
        key = ts_key(event.channel, event.ts)
        try:
            record = self.record
            self_dm = record.self_dm_channel_id if record else None
            if self_dm and event.channel == self_dm:
                self.state.advance_cursor(self_dm, event.ts)
            decision = classify(
                event, self_dm=self_dm, owner=record.owner_user_id if record else None,
                find_thread=self._find_thread,
                resolve_handle=self._relay_service.handles.get_session,
            )
            if decision.kind == KIND_IGNORE:
                # The Content seam: never reached for an instruction candidate, never an instruction.
                matched = self._monitor_match(event)
                if matched:
                    self.state.mark_seen(event.channel, event.ts)
                    self.coalescer.add(event, matched)
                    self.state.schedule_flush()
                else:
                    self._drop(decision.reason or "ignored")
                    if decision.reason in _CANDIDATE_IGNORES:
                        # an owner self-DM message that passed the trust rule but is not an
                        # instruction: remembered, like in 3a, so it is never re-classified later
                        self.state.mark_seen(event.channel, event.ts)
                    if self_dm and event.channel == self_dm:
                        self.state.schedule_flush()  # the self-DM cursor moved; nothing else needs a write
                return False
            self.state.mark_seen(event.channel, event.ts)
            self.state.inflight[key] = {
                "key": key, "event": event.to_dict(), "decision": decision.to_dict(),
                "reply_done": False, "accepted_at": self._now(),
            }
            await self.state.flush(strict=True)  # durable before the ack, or no ack at all
        except BaseException:
            # not persisted / not classified: Slack redelivers (no ack) and must not hit the dedupe
            self.state.inflight.pop(key, None)
            self.state.unmark_seen(event.channel, event.ts)
            raise
        self._queue.put_nowait(key)
        return True

    def _learn_app_id(self, event: RelayEvent) -> None:
        if event.api_app_id and self.state.own_app_id != event.api_app_id:
            self.state.own_app_id = event.api_app_id
            self.state.schedule_flush()

    def _match_context(self) -> MatchContext:
        record = self.record
        return MatchContext(
            owner_user_id=record.owner_user_id if record else None,
            self_dm=record.self_dm_channel_id if record else None,
            own_app_id=self.state.own_app_id,
            is_instruction_thread=lambda c, t: self.store.find_thread(c, t) is not None,
            now=self._now(),
        )

    def _monitor_match(self, event: RelayEvent) -> list[dict[str, Any]]:
        """Monitors this event feeds; local and index-based (no scan, no I/O)."""
        candidates = self.store.monitors_for_channel(event.channel)
        if not candidates:
            return []
        ctx = self._match_context()
        reason = never_content(event, ctx)
        if reason:
            self._drop(f"monitor_{reason}")
            return []
        return match_monitors(event, candidates, ctx)

    def monitor_key_seen(self, monitor_id: str, key: str) -> bool:
        return key in self._recent_keys.get(monitor_id, ())

    def note_monitor_key(self, monitor_id: str, key: str) -> None:
        """Remember a message handled for one monitor (bounded): a live twin of a backfilled message,
        or a redelivery, is then one delivery per monitor, without touching the shared ring."""
        keys = self._recent_keys.setdefault(monitor_id, OrderedDict())
        keys[key] = None
        keys.move_to_end(key)
        while len(keys) > const.MONITOR_RECENT_KEYS:
            keys.popitem(last=False)

    def buffer_lost(self, buf: SessionBuffer) -> None:
        """A buffer was dropped without being delivered (stop, dispose, flush failure): the in-memory
        high water falls back to the durable cursor so the next backfill re-fetches its content."""
        for mid in {mid for m in buf.messages.values() for mid in m.monitor_ids} | set(buf.cover):
            rec = self.store.get_monitor(mid)
            if rec is not None:
                self._high_water[mid] = rec.get("last_seen_ts") or self._ts_str(self._now())
                self._hold.add(mid)
                keys = self._recent_keys.get(mid)
                if keys:
                    for key in [f"{m.event.channel}:{m.event.ts}" for m in buf.messages.values()]:
                        keys.pop(key, None)

    def note_high_water(self, monitor_id: str, ts: str) -> None:
        if _tsf(ts) > _tsf(self._high_water.get(monitor_id)):
            self._high_water[monitor_id] = ts

    async def _rec(self, **fields: Any) -> None:
        """Audit a monitor event, naming the session (name + handle) so the viewer can show it."""
        session_id = fields.get("session_id")
        if session_id and "session_name" not in fields:
            try:
                info = await self._sessions.get_session_info(session_id)
            except Exception:
                info = None
            if info is not None:
                fields["session_name"] = getattr(info, "name", None)
                fields.setdefault("handle", (getattr(info, "config", None) or {}).get("relay_handle"))
        await self._audit.record(self.relay_id, **fields)

    def spawn_audit(self, **fields: Any) -> None:
        """Fire-and-forget audit write from synchronous code (never raises)."""
        task = self._spawn(self._rec(**fields))
        if task is not None:
            self._flush_audits.add(task)
            task.add_done_callback(self._flush_audits.discard)

    def _find_thread(self, channel: str, thread_ts: str) -> dict[str, Any] | None:
        """The owned thread record, or a pending one: a top-level instruction that is accepted but
        not yet processed owns its thread, so a quick follow-up is not lost to the worker lag."""
        record = self.store.find_thread(channel, thread_ts)
        if record is not None:
            return record
        pending = self.state.inflight.get(ts_key(channel, thread_ts))
        d = (pending or {}).get("decision") or {}
        if d.get("kind") == KIND_INSTRUCTION and d.get("top_level") and d.get("session_id"):
            return {"id": None, "session_id": d["session_id"], "status": "active"}
        return None

    # ------------------------------------------------------------------ worker

    async def _worker(self) -> None:
        while True:
            key = await self._queue.get()
            entry = self.state.inflight.get(key)
            if entry is None:
                continue
            try:
                await self._handle(entry)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Inbound instruction processing failed")
                await self._finish_error(entry)
            self.state.inflight.pop(key, None)
            await self.state.flush()

    async def _finish_error(self, entry: dict[str, Any]) -> None:
        event = RelayEvent.from_dict(entry["event"])
        d = entry["decision"]
        await self._retract_ack(entry, event, d)
        await self._audit_instruction(event, d, "error", "internal_error")
        await self._reply_once(entry, event, d.get("thread_ts") or event.ts, "delivery_failed")

    async def _reply_once(
        self, entry: dict[str, Any], event: RelayEvent, thread_ts: str, code: str
    ) -> None:
        if entry.get("reply_done"):
            return
        entry["reply_done"] = True  # at most one reply per source event, even across a crash
        await self.state.flush()
        ts = await self._responder.reply(self.relay_id, event.channel, thread_ts, code)
        await self._audit.record(
            self.relay_id, event="inbound_reply" if ts else "inbound_reply_failed",
            outcome="ok" if ts else "error", code=code, channel_id=event.channel,
            thread_ts=thread_ts, message_ts=ts,
        )

    async def _audit_instruction(
        self, event: RelayEvent, d: dict[str, Any], outcome: str, code: str | None = None
    ) -> None:
        await self._audit.record(
            self.relay_id, event="inbound_instruction", outcome=outcome, code=code,
            session_id=d.get("session_id"), handle=d.get("handle"), channel_id=event.channel,
            thread_ts=d.get("thread_ts"), message_ts=event.ts, **text_fields(event.text),
        )

    async def _check_session(self, session_id: str) -> str | None:
        info = await self._sessions.get_session_info(session_id)
        if info is None:
            return "session_gone"
        if getattr(info, "is_ephemeral", False):
            return "ephemeral"
        if is_errored(info.state):
            return "session_error"
        attachment = await self._relay_service.effective_attachment(info)
        if attachment["relay_id"] != self.relay_id:
            return "not_attached"
        if not self._manager.has_token(self.relay_id, "user"):
            return "no_token"
        return None

    async def _handle(self, entry: dict[str, Any]) -> None:
        if entry.get("delivered"):  # re-run after a stop/crash that came after the enqueue
            return
        event = RelayEvent.from_dict(entry["event"])
        d = entry["decision"]
        thread_ts = d.get("thread_ts") or event.ts
        try:
            age = self._now() - float(event.ts)
        except ValueError:
            age = 0.0
        if age > const.INSTRUCTION_MAX_AGE_SECONDS:
            await self._audit_instruction(event, d, "late", "received_late")
            if self._relay_bucket.allow():
                await self._reply_once(entry, event, thread_ts, "received_late")
            return
        if d["kind"] == KIND_REPLY:
            await self._audit_instruction(event, d, "refused", d.get("reason"))
            await self._reply_once(entry, event, thread_ts, d["reason"])
            return
        # rate limit accepted instructions: over the limit -> drop, audit once per window, no reply
        if not self._relay_bucket.allow() or not self._thread_buckets.allow(
            ts_key(event.channel, thread_ts)
        ):
            if self._mono() - self._rate_audit_at > 60:
                self._rate_audit_at = self._mono()
                await self._audit_instruction(event, d, "rate_limited", "rate_limited")
            return
        session_id = d["session_id"]
        record = self.store.find_thread(event.channel, thread_ts) if not d.get("top_level") else None
        if record is not None and record.get("status") == SUSPENDED:
            await self._audit_instruction(event, d, "refused", "session_inactive")
            if not record.get("notified_inactive"):
                await self.store.update(self.relay_id, record["id"], notified_inactive=True)
                await self._reply_once(entry, event, thread_ts, "session_inactive")
            return
        code = await self._check_session(session_id)
        if code:
            await self._audit_instruction(event, d, "refused", code)
            await self._reply_once(entry, event, thread_ts, code)
            return
        created = False
        if d.get("top_level"):
            created = self.store.find_thread(event.channel, event.ts) is None
            record = await self.store.create_thread(session_id, event.channel, event.ts, event.ts)
            await self._enforce_cap()
        await self._ack_received(entry, event, d)
        relay_record = self.record
        dispatch = format_instruction(
            relay_record.name if relay_record else "Slack", event.channel, thread_ts, d["text"],
            event.file_count,
        )
        try:
            outcome = await self._sink(
                session_id,
                comm_id=comm_id_for(self.relay_id, event.channel, event.ts),
                summary=instruction_summary(d["text"]),
                content=d["text"],
                dispatch_content=dispatch,
                metadata={"relay_inbound": {
                    "relay_id": self.relay_id, "channel": event.channel, "thread_ts": thread_ts,
                    "message_ts": event.ts, "kind": "instruction",
                }},
                relay_id=self.relay_id,
            )
        except Exception:
            logger.exception("Inbound delivery failed")
            outcome = DeliveryOutcome("failed", "exception")
        if outcome.status == "failed":
            if created and record is not None:  # no thread (and no reply exemption) without delivery
                await self.store.remove(self.relay_id, record["id"])
            await self._retract_ack(entry, event, d)
            await self._audit_instruction(event, d, "refused", "delivery_failed")
            await self._reply_once(entry, event, thread_ts, "delivery_failed")
            return
        self._ack_tasks.pop(entry["key"], None)
        if outcome.status == "delivered":  # follow it to its outcome: a later failure / cancel is reported
            comm_id = comm_id_for(self.relay_id, event.channel, event.ts)
            self.watcher.watch(WatchEntry(
                "instruction", session_id, comm_id, self._now(),
                info={"channel": event.channel, "thread_ts": thread_ts, "message_ts": event.ts,
                      "handle": d.get("handle")},
            ))
        entry["delivered"] = True  # persisted: a re-run never enqueues this instruction again
        await self.state.flush()
        if record is not None and record.get("id"):
            await self.store.touch_thread(
                record["id"], last_seen_ts=event.ts if event.ts != thread_ts else None
            )
        await self._audit_instruction(
            event, d, "deduped" if outcome.status == "already_delivered" else "delivered"
        )

    # ------------------------------------------------- received reaction (issue #2364)

    async def _ack_received(self, entry: dict[str, Any], event: RelayEvent, d: dict[str, Any]) -> None:
        """Add the received reaction to an instruction that passed every check and is about to be
        enqueued. Reached ONLY from ``_handle`` after the age window, refusal, rate-limit, session
        and relay checks, and only for a self-DM instruction decision: ignored, refused, late,
        rate-limited, replayed and (3b) monitored-content events never get here, so they never get
        a reaction. This stays true for the future Content branch: it must not call this.

        At most once: ``acked`` is persisted (fsync) BEFORE the Slack call, so a restart, in-flight
        re-run or backfill replay never adds it again (and never re-adds one the owner removed); a
        crash in between loses the reaction, never doubles it. The call runs as a tracked
        background task with a hard timeout and no retry, so Slack can neither delay nor fail the
        enqueue."""
        record = self.record
        if (
            entry.get("acked") or d.get("kind") != KIND_INSTRUCTION
            or record is None or event.channel != record.self_dm_channel_id
        ):
            return
        entry["acked"] = True
        await self.state.flush()
        task = self._spawn(self._add_reaction(entry, event, d))
        if task is not None:
            self._ack_tasks[entry["key"]] = task  # popped on delivery or retraction

    async def _add_reaction(self, entry: dict[str, Any], event: RelayEvent, d: dict[str, Any]) -> None:
        outcome, code = "ok", None
        try:
            await asyncio.wait_for(
                self._manager.adapter_for(self.relay_id).react(
                    event.channel, event.ts, const.INBOUND_ACK_REACTION, retry=False
                ),
                const.ACK_TIMEOUT_SECONDS,
            )
        except RelayError as e:
            if e.code == "already_reacted":
                code = e.code  # present already: success, but not ours to remove
            else:
                outcome, code = "error", safe_error_code(e.code)
        except TimeoutError:
            outcome, code = "error", "timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Inbound reaction failed")
            outcome, code = "error", "internal_error"
        if outcome == "ok" and code is None:
            entry["ack_added"] = True  # persisted with the entry: only then may a retraction remove it
            self.state.schedule_flush()
        await self._audit_ack(event, d, outcome, code)

    async def _retract_ack(self, entry: dict[str, Any], event: RelayEvent, d: dict[str, Any]) -> None:
        """Delivery failed after the reaction was (or may have been) added: remove it, best effort."""
        if not entry.get("acked") or entry.get("delivered"):
            return
        task = self._ack_tasks.pop(entry.get("key"), None)
        if task is not None:  # bounded by ACK_TIMEOUT_SECONDS
            await asyncio.gather(task, return_exceptions=True)
        if not entry.get("ack_added"):
            return  # our add did not happen (failed, already present, lost in a crash): not ours to remove
        outcome, code = "removed", None
        try:
            await asyncio.wait_for(
                self._manager.adapter_for(self.relay_id).unreact(
                    event.channel, event.ts, const.INBOUND_ACK_REACTION, retry=False
                ),
                const.ACK_TIMEOUT_SECONDS,
            )
        except RelayError as e:
            if e.code != "no_reaction":
                outcome, code = "error", safe_error_code(e.code)
        except TimeoutError:
            outcome, code = "error", "timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Inbound reaction removal failed")
            outcome, code = "error", "internal_error"
        await self._audit_ack(event, d, outcome, code)

    async def _audit_ack(
        self, event: RelayEvent, d: dict[str, Any], outcome: str, code: str | None
    ) -> None:
        await self._audit.record(
            self.relay_id, event="inbound_ack", outcome=outcome, code=code,
            session_id=d.get("session_id"), handle=d.get("handle"), channel_id=event.channel,
            message_ts=event.ts,
        )

    async def _enforce_cap(self) -> None:
        expired, evicted = await self.store.housekeeping()
        if expired or evicted:
            await self._audit.record(
                self.relay_id, event="inbound_housekeeping", outcome="ok", count=expired + evicted,
                code="expired" if expired and not evicted else "evicted",
            )

    # ---------------------------------------------------------------- backfill

    def _start_backfill(self) -> None:
        if self._stopping or (self._backfill_task is not None and not self._backfill_task.done()):
            return
        self._backfill_task = asyncio.get_running_loop().create_task(
            self._backfill_safe(), name=f"inbound-backfill-{self.relay_id}"
        )

    async def _backfill_safe(self) -> None:
        try:
            await self.run_backfill()
        except asyncio.CancelledError:
            raise
        except RelayError as e:
            logger.warning("Inbound backfill failed (%s)", safe_error_code(e.code))
        except Exception:
            logger.exception("Inbound backfill failed")
        try:
            await self.backfill_monitors()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitor backfill failed")

    async def run_backfill(self) -> None:
        """On every (re)connect: self-DM history since the cursor, plus owned-thread replies."""
        record = self.record
        if record is None or not record.self_dm_channel_id:
            return
        channel = record.self_dm_channel_id
        cursor = self.state.cursors.get(channel)
        if cursor is None:  # first start: now, no backfill
            self.state.cursors[channel] = f"{self._now():.6f}"
            self.state.schedule_flush()
            return
        adapter = self._manager.adapter_for(self.relay_id)
        messages = []
        page_cursor: str | None = None
        truncated = False
        for page_no in range(const.BACKFILL_MAX_PAGES):
            page = await adapter.read_history(
                channel, limit=const.BACKFILL_PAGE_SIZE, oldest=cursor, cursor=page_cursor
            )
            messages.extend(page.messages)
            page_cursor = page.next_cursor
            if not page_cursor:
                break
            truncated = page_no == const.BACKFILL_MAX_PAGES - 1
        if truncated:
            await self._audit.record(
                self.relay_id, event="inbound_housekeeping", outcome="truncated",
                code="backfill_truncated", channel_id=channel, count=len(messages),
            )
        for msg in sorted(messages, key=lambda m: _tsf(m.ts)):
            await self._accept_backfilled(msg, channel)
        for thread in await self.store.list(self.relay_id):
            if thread.get("kind") != const.THREAD_KIND or thread.get("status") != ACTIVE:
                continue
            await self._backfill_thread(adapter, thread)

    async def _backfill_thread(self, adapter, thread: dict[str, Any]) -> None:
        last = thread.get("last_seen_ts") or thread["thread_ts"]
        collected = []
        page_cursor: str | None = None
        for _ in range(const.BACKFILL_MAX_PAGES):
            page = await adapter.read_thread(
                thread["channel"], thread["thread_ts"], const.BACKFILL_PAGE_SIZE, page_cursor
            )
            collected.extend(page.messages)
            page_cursor = page.next_cursor
            if not page_cursor:
                break
        for msg in sorted(collected, key=lambda m: _tsf(m.ts)):
            if msg.ts == thread["thread_ts"] or _tsf(msg.ts) <= _tsf(last):
                continue
            if msg.thread_ts is None:
                msg.thread_ts = thread["thread_ts"]
            await self._accept_backfilled(msg, thread["channel"])

    async def _accept_backfilled(self, msg, channel: str) -> None:
        event = event_from_message(self.relay_id, msg, channel)
        if isinstance(event, Drop):
            self._drop(event.reason)
            return
        await self.accept_event(event)

    # ------------------------------------------------------------ housekeeping

    async def run_housekeeping(self) -> None:
        await self._enforce_cap()
        for gone in await self.store.expire_monitors():
            self._forget_monitor(gone["id"])
            await self._rec(
                event="monitor_expired", outcome="ok", code=gone.get("_expired_reason"),
                session_id=gone["session_id"], monitor_id=gone["id"], monitor_kind=gone["kind"],
                channel_id=gone.get("channel"),
            )

    async def _flush_drop_summary(self) -> None:
        if not self._drops:
            return
        counts, self._drops = dict(self._drops), Counter()
        await self._audit.record(
            self.relay_id, event="inbound_drop_summary", outcome="ok",
            count=sum(counts.values()), reason_counts=counts,
        )

    async def _housekeeping_loop(self) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(const.DROP_SUMMARY_INTERVAL_SECONDS)
            ticks += 1
            await self._flush_drop_summary()
            if ticks % 2 == 0:
                await self.run_housekeeping()

    # -------------------------------------------------------- session hooks (D6)

    async def on_session_deleted(self, session_id: str) -> None:
        for m in self.store.monitors(session_id):
            self._forget_monitor(m["id"])
        self.coalescer.discard_session(session_id)
        self.watcher.drop_session(session_id)
        removed = await self.store.remove_by_session(session_id)
        if removed:
            await self._rec(
                event="monitor_removed", outcome="ok", session_id=session_id,
                source="session", count=removed,
            )
        for key, entry in list(self.state.inflight.items()):
            if (entry.get("decision") or {}).get("session_id") == session_id:
                self.state.inflight.pop(key, None)
                self._ack_tasks.pop(key, None)
        await self.state.flush()

    async def on_session_disposed(self, session_id: str) -> None:
        """Suspend only ACTIVE records (another suspension reason is not overwritten; the owner
        ``paused`` flag is never touched). Buffered content is dropped; the durable cursors stay."""
        self.coalescer.discard_session(session_id)
        self.watcher.drop_session(session_id)
        for m in self.store.monitors(session_id):
            if m["status"] == ACTIVE:
                await self._suspend(m, const.SUSPEND_DISPOSED)
        await self.store.set_status_by_session(
            session_id, SUSPENDED, reason=const.SUSPEND_DISPOSED, only_status=ACTIVE,
            kinds=frozenset({const.THREAD_KIND}),
        )

    async def on_session_started(self, session_id: str) -> None:
        """Restore ONLY disposed-suspended records, then re-run the policy check (the channel may
        have been blocked meanwhile) and fetch what happened while disposed (24 h cap)."""
        restored = [
            m for m in self.store.monitors(session_id)
            if m["status"] == SUSPENDED and m.get("suspended_reason") == const.SUSPEND_DISPOSED
        ]
        await self.store.set_status_by_session(
            session_id, ACTIVE, only_reason=const.SUSPEND_DISPOSED,
            kinds=frozenset({const.THREAD_KIND, *const.MONITOR_KINDS}),
        )
        for m in restored:
            self._high_water[m["id"]] = m.get("last_seen_ts") or self._ts_str(self._now())
            self._hold.discard(m["id"])
            await self._rec(
                event="monitor_resumed", outcome="ok", session_id=session_id,
                monitor_id=m["id"], monitor_kind=m["kind"], channel_id=m.get("channel"),
                source="session_start",
            )
        if self.store.monitors(session_id):
            await self.reevaluate(session_id)
            if restored:
                self._spawn(self.backfill_monitors(session_id))

    def has_thread_grant(self, session_id: str, channel: str, thread_ts: str) -> bool:
        record = self.record
        if record is None or not record.inbound_enabled:
            return False
        return self.store.has_active_thread(session_id, channel, thread_ts)


    # ======================================================= monitors (stage 3b-i)

    def monitor_eligibility(self) -> str | None:
        """None when monitors can work on this relay, else a sanitised reason."""
        record = self.record
        if record is None:
            return "relay_missing"
        if not record.inbound_enabled:
            return "disabled_by_owner"
        if not record.app_token_secret or not self._manager.has_token(self.relay_id, "app"):
            return "no_app_token"
        if not self._manager.has_token(self.relay_id, "user"):
            return "no_user_token"
        return None

    @staticmethod
    def _ts_str(now: float) -> str:
        return f"{now:.6f}"

    async def create_monitor(
        self, session_id: str, kind: str, channel: str, *, thread_ts: str | None,
        filters: dict[str, Any], expires_at: float | None,
    ) -> tuple[dict[str, Any], str]:
        record, outcome = await self.store.create_monitor(
            session_id, kind, channel, thread_ts=thread_ts, filters=filters, expires_at=expires_at,
        )
        self._high_water[record["id"]] = record["last_seen_ts"]
        if outcome in ("created", "resumed"):
            keywords = filters.get("keywords") or []
            await self._rec(
                event="monitor_created" if outcome == "created" else "monitor_resumed",
                outcome="ok", session_id=session_id, monitor_id=record["id"], monitor_kind=kind,
                channel_id=channel, thread_ts=thread_ts, count=len(keywords),
                query_sha256=hashlib.sha256("\n".join(keywords).encode()).hexdigest() if keywords else None,
                source="agent",
            )
        return record, outcome

    async def remove_monitor(self, session_id: str, monitor_id: str, *, source: str = "agent") -> bool:
        """Remove one of *session_id*'s own monitors."""
        rec = self.store.get_monitor(monitor_id)
        if rec is None or rec.get("session_id") != session_id:
            return False
        await self.store.remove_monitor(monitor_id)
        self._forget_monitor(monitor_id)
        await self._cancel_pending_for({monitor_id})
        await self._rec(
            event="monitor_removed", outcome="ok", session_id=session_id,
            monitor_id=monitor_id, monitor_kind=rec["kind"], channel_id=rec.get("channel"),
            source=source,
        )
        return True

    def _forget_monitor(self, monitor_id: str) -> None:
        self._high_water.pop(monitor_id, None)
        self._hold.discard(monitor_id)
        self._recent_keys.pop(monitor_id, None)
        self.coalescer.discard_monitor(monitor_id)

    def list_monitors(self, session_id: str) -> list[dict[str, Any]]:
        return [dict(m) for m in self.store.monitors(session_id)]

    # ---------------------------------------------------------- policy at delivery

    async def _session_problem(self, session_id: str) -> str | None:
        """None when the monitoring session can receive content now, else a short reason."""
        info = await self._sessions.get_session_info(session_id)
        if info is None:
            return "session_gone"
        if getattr(info, "is_ephemeral", False):
            return "ephemeral"
        if is_errored(info.state):
            return "session_error"
        attachment = await self._relay_service.effective_attachment(info)
        if attachment["relay_id"] != self.relay_id:
            return "not_attached"
        if not self._manager.has_token(self.relay_id, "user"):
            return "no_token"
        return None

    async def _desired_state(
        self, monitor: dict[str, Any], problem: str | None, policies: dict[str, Any]
    ) -> str | None:
        """None = the monitor may be active, else the suspension reason."""
        if problem:
            return const.SUSPEND_SESSION_INACTIVE
        eff = policies.get(monitor.get("channel"))
        policy = eff.policy if eff else None
        if policy == "blocked":
            return const.SUSPEND_CHANNEL_BLOCKED
        if policy is None:
            return const.SUSPEND_POLICY_UNSET
        return None

    async def apply_policy(
        self, session_id: str, *, resume: bool, only: set[str] | None = None
    ) -> set[str]:
        async with self._policy_lock:
            return await self._apply_policy(session_id, resume=resume, only=only)

    async def _apply_policy(
        self, session_id: str, *, resume: bool, only: set[str] | None = None
    ) -> set[str]:
        """Fresh policy + session check of the session's monitors (never cached). Suspends monitors
        whose channel is now blocked / unset or whose session cannot receive content; with
        ``resume`` also lifts those suspensions (from now, D4). Returns the ids that are active."""
        monitors = [m for m in self.store.monitors(session_id) if only is None or m["id"] in only]
        if not monitors:
            return set()
        problem = await self._session_problem(session_id)
        policies: dict[str, Any] = {}
        if problem is None:
            info = await self._sessions.get_session_info(session_id)
            policies = await self._relay_service.effective_relay_policies(info, self.relay_id)
        valid: set[str] = set()
        for m in monitors:
            desired = await self._desired_state(m, problem, policies)
            if desired is None:
                if m["status"] == SUSPENDED and m.get("suspended_reason") in const.AUTO_RESUME_REASONS:
                    if not resume:
                        continue
                    now = self._now()
                    await self.store.set_monitor_status(m["id"], ACTIVE, resume_from=now)
                    self._high_water[m["id"]] = self._ts_str(now)
                    self._hold.discard(m["id"])
                    await self._rec(
                        event="monitor_resumed", outcome="ok", session_id=session_id,
                        monitor_id=m["id"], monitor_kind=m["kind"], channel_id=m.get("channel"),
                        source="policy",
                    )
                elif m["status"] != ACTIVE:
                    continue
                if not m.get("paused"):
                    valid.add(m["id"])
            elif m["status"] == ACTIVE:
                await self._suspend(m, desired)
        return valid

    async def _suspend(self, monitor: dict[str, Any], reason: str) -> None:
        await self.store.set_monitor_status(monitor["id"], SUSPENDED, reason)
        if reason in _CANCELLING_REASONS:  # content already queued for this monitor must not arrive
            await self._cancel_pending_for({monitor["id"]})
        await self._rec(
            event="monitor_suspended", outcome="ok", code=reason,
            session_id=monitor["session_id"], monitor_id=monitor["id"], monitor_kind=monitor["kind"],
            channel_id=monitor.get("channel"),
        )

    async def reevaluate(self, session_id: str) -> None:
        await self.apply_policy(session_id, resume=True)

    async def reevaluate_all(self) -> None:
        for session_id in list(dict.fromkeys(m["session_id"] for m in self.store.monitors())):
            try:
                await self.reevaluate(session_id)
            except Exception:
                logger.exception("Monitor policy re-evaluation failed")

    async def _cancel_pending_for(self, monitor_ids: set[str]) -> None:
        """Cancel queued, not yet dispatched envelopes that carry content of these monitors. The
        watcher then reports them ``cancelled``; because we caused it, that neither counts as a
        delivery failure nor suspends anything, and the remaining monitors' content is re-fetched."""
        for entry in list(self.watcher.entries.values()):
            if entry.kind != "monitor" or not monitor_ids & set(entry.cover):
                continue
            entry.info["self_cancel"] = True
            try:
                cancelled = await self.port.cancel(entry.session_id, entry.comm_id)
            except Exception:
                logger.exception("Monitor envelope cancel failed")
                cancelled = False
            if not cancelled:
                entry.info.pop("self_cancel", None)  # already dispatched or gone: nothing to undo

    # ---------------------------------------------------------------- flush

    async def flush_buffer(self, buf: SessionBuffer) -> None:
        """Build and enqueue ONE envelope from a detached buffer (see ``Coalescer``)."""
        session_id = buf.session_id
        mids = {mid for m in buf.messages.values() for mid in m.monitor_ids} | set(buf.cover)
        valid = await self.apply_policy(session_id, resume=False, only=mids)
        kept: list[BufferedMessage] = []
        for m in sorted(buf.messages.values(), key=lambda x: _tsf(x.event.ts)):
            ids = [i for i in m.monitor_ids if i in valid]
            if ids:
                kept.append(BufferedMessage(m.event, ids))
        included, omitted = monitor_content.select_for_envelope(kept)
        cover = {k: v for k, v in buf.cover.items() if k in valid}
        for m in kept:
            for mid in m.monitor_ids:
                if _tsf(m.event.ts) > _tsf(cover.get(mid)):
                    cover[mid] = m.event.ts
        await self._flood_windows(buf, mids | set(buf.flooded))
        omitted_by_channel: Counter = Counter(buf.overflow)
        for m in omitted:
            omitted_by_channel[m.event.channel] += 1
        if not included:
            self._advance_unqueued_cover(cover)
            return
        entries = await self._build_entries(included)
        monitor_ids = sorted({mid for m in included for mid in m.monitor_ids})
        minutes = round((self._now() - buf.opened_at) / 60) or 1
        dispatch = monitor_content.build_dispatch(
            entries, monitor_count=len(monitor_ids), minutes=minutes,
            omitted_by_channel=dict(omitted_by_channel), older_omitted=buf.older_omitted,
        )
        summary = monitor_content.monitor_summary(len(included))
        keys = [f"{m.event.channel}:{m.event.ts}" for m in included]
        attempt = 0
        comm_id = monitor_comm_id(self.relay_id, session_id, keys)
        while self.port.item_status(session_id, comm_id) not in (None, "pending") and attempt < 50:
            attempt += 1  # the earlier comm of this exact content already ended: a retry is a new comm
            comm_id = monitor_comm_id(self.relay_id, session_id, keys, attempt)
        omitted_total = sum(omitted_by_channel.values())
        metadata = {
            "comm": {
                "from_name": "slack-monitor", "from_display_name": "Slack monitor",
                "comm_type": "info", "summary": summary, "content": summary,
            },
            "relay_inbound": {
                "relay_id": self.relay_id, "kind": "monitor", "monitor_ids": monitor_ids,
                "count": len(included), "omitted": omitted_total,
            },
        }
        counts: dict[str, int] = {}
        for m in included:
            for mid in m.monitor_ids:
                counts[mid] = counts.get(mid, 0) + 1
        # Watched BEFORE the enqueue so a block / unmonitor that lands meanwhile can cancel it; the
        # watcher skips the entry until the enqueue has finished.
        entry = WatchEntry(
            "monitor", session_id, comm_id, self._now(), cover=cover,
            info={"counts": counts, "count": len(included), "omitted": omitted_total, "enqueuing": True},
        )
        self.watcher.watch(entry)
        try:
            outcome = await self.port.enqueue(
                session_id, comm_id=comm_id, summary=summary, content=summary,
                dispatch_content=dispatch, metadata=metadata, relay_id=self.relay_id,
            )
        except Exception:
            logger.exception("Monitor delivery failed")
            outcome = DeliveryOutcome("failed", "exception")
        if outcome.status == "failed":
            self.watcher.entries.pop(comm_id, None)
            await self._monitor_failed(
                list(cover), comm_id, outcome.error or "enqueue_failed", len(included)
            )
            return
        self.coalescer.envelopes.record(session_id)
        entry.info.pop("enqueuing", None)
        # a monitor removed / suspended while we were building and enqueueing: undo, do not deliver
        stale = {
            mid for mid in cover
            if (rec := self.store.get_monitor(mid)) is None or rec["status"] != ACTIVE
        }
        if stale:
            await self._cancel_pending_for(stale)

    def _advance_unqueued_cover(self, cover: dict[str, str]) -> None:
        """Nothing was delivered but messages were consumed (flood-dropped / tallied): move the
        durable cursor past them, unless an earlier envelope of the monitor is still unconfirmed
        (its failure must not skip its content)."""
        outstanding = {
            mid for e in self.watcher.entries.values() if e.kind == "monitor" for mid in e.cover
        }
        for mid, ts in cover.items():
            if mid not in outstanding and mid not in self._hold:
                self.store.advance_cursor(mid, ts)

    async def _flood_windows(self, buf: SessionBuffer, monitor_ids: set[str]) -> None:
        for mid in monitor_ids:
            rec = self.store.get_monitor(mid)
            if rec is None or rec["status"] != ACTIVE:
                continue
            if mid in buf.flooded:
                windows = int(rec.get("flood_windows") or 0) + 1
                self.store.touch_monitor(mid, flood_windows=windows)
                if windows >= const.FLOOD_SUSPEND_WINDOWS:
                    await self._suspend(rec, const.SUSPEND_FLOOD)
            elif rec.get("flood_windows"):
                self.store.touch_monitor(mid, flood_windows=0)

    async def _build_entries(self, included: list[BufferedMessage]) -> list[dict[str, Any]]:
        record = self.record
        owner = record.owner_user_id if record else None
        workspace_url = getattr(record, "workspace_url", None)
        labels: dict[str, str] = {}
        users: dict[str, str | None] = {}
        names = self._names
        if names is not None:
            adapter = self._manager.adapter_for(self.relay_id)

            async def resolve() -> None:
                for channel in dict.fromkeys(m.event.channel for m in included):
                    labels[channel] = await names.channel_label(adapter, self.relay_id, channel, owner)
                users.update(await names.user_names(
                    adapter, self.relay_id, (m.event.user for m in included)
                ))

            try:
                await asyncio.wait_for(resolve(), const.LABEL_TIMEOUT_SECONDS)
            except Exception:  # IDs on failure: names never block or fail a delivery
                logger.debug("Monitor label resolution failed", exc_info=True)
        entries = []
        for m in included:
            e = m.event
            entries.append(monitor_content.entry(
                monitor_ids=list(m.monitor_ids), channel=e.channel,
                channel_label=labels.get(e.channel), ts=e.ts, thread_ts=e.thread_ts, user=e.user,
                user_name=users.get(e.user) if e.user else None,
                is_owner=bool(owner and e.user == owner),
                bot=bool(e.app_id or e.bot_id or e.is_agent), text=e.text, files=e.file_count,
                permalink=monitor_content.permalink(workspace_url, e.channel, e.ts, e.thread_ts),
            ))
        return entries

    # ------------------------------------------------- delivery outcome (cursor-on-confirm)

    async def _on_watch_outcome(self, entry: WatchEntry, status: str) -> None:
        if entry.kind == "monitor":
            await self._monitor_outcome(entry, status)
        else:
            await self._instruction_outcome(entry, status)

    async def _monitor_outcome(self, entry: WatchEntry, status: str) -> None:
        if status == "delivered":
            now = self._now()
            for mid, ts in entry.cover.items():
                if mid not in self._hold:
                    self.store.advance_cursor(mid, ts)
                self.store.touch_monitor(
                    mid, last_delivered_at=now, last_error=None, failures=0, last_activity_at=now,
                )
                self.store.bump_counters(mid, delivered=int(entry.info.get("counts", {}).get(mid, 0)))
            await self._rec(
                event="monitor_delivered", outcome="ok", session_id=entry.session_id,
                envelope_id=entry.comm_id, count=entry.info.get("count"),
                omitted=entry.info.get("omitted"),
            )
        elif status == STATUS_GONE:
            for mid in entry.cover:
                self.store.touch_monitor(mid, last_error="delivery_unknown")
        elif status == "cancelled" and entry.info.get("self_cancel"):
            # we cancelled it (block / unmonitor): not a failure. The cursor stays, the monitors that
            # are still active re-collect their content from it.
            for mid in entry.cover:
                rec = self.store.get_monitor(mid)
                if rec is not None:
                    self._hold.add(mid)
                    self._high_water[mid] = rec.get("last_seen_ts") or self._ts_str(self._now())
            self._spawn(self.backfill_monitors(entry.session_id))
        else:  # failed | cancelled
            await self._monitor_failed(
                list(entry.cover), entry.comm_id, status, int(entry.info.get("count") or 0),
                session_id=entry.session_id,
            )

    async def _monitor_failed(
        self, monitor_ids: list[str], comm_id: str, error: str, count: int, *,
        session_id: str | None = None,
    ) -> None:
        """The envelope did not reach the session: the durable cursor stays (the next backfill
        re-fetches it) and the in-memory high water drops back to it."""
        for mid in monitor_ids:
            rec = self.store.get_monitor(mid)
            if rec is None:
                continue
            failures = int(rec.get("failures") or 0) + 1
            self.store.touch_monitor(mid, failures=failures, last_error=safe_error_code(error)[:60])
            self._hold.add(mid)
            self._high_water[mid] = rec.get("last_seen_ts") or self._ts_str(self._now())
            if failures >= const.DELIVERY_FAILURES_TO_SUSPEND and rec["status"] == ACTIVE:
                await self._suspend(rec, const.SUSPEND_DELIVERY_FAILED)
        await self._rec(
            event="monitor_delivery_failed", outcome="error",
            code=safe_error_code(error)[:60], session_id=session_id, envelope_id=comm_id, count=count,
        )

    async def _instruction_outcome(self, entry: WatchEntry, status: str) -> None:
        """A 3a instruction that was enqueued and then failed / was cancelled: ONE marked reply."""
        if status not in ("failed", "cancelled") or entry.comm_id in self._replied:
            return
        self._replied.add(entry.comm_id)
        if len(self._replied) > 2000:
            self._replied.pop()
        info = entry.info
        code = "delivery_failed" if status == "failed" else "cancelled"
        await self._audit.record(
            self.relay_id, event="inbound_instruction", outcome=code, code=code,
            session_id=entry.session_id, handle=info.get("handle"), channel_id=info.get("channel"),
            thread_ts=info.get("thread_ts"), message_ts=info.get("message_ts"),
        )
        ts = await self._responder.reply(self.relay_id, info["channel"], info["thread_ts"], code)
        await self._audit.record(
            self.relay_id, event="inbound_reply" if ts else "inbound_reply_failed",
            outcome="ok" if ts else "error", code=code, channel_id=info["channel"],
            thread_ts=info["thread_ts"], message_ts=ts,
        )

    # ------------------------------------------------------------ monitor backfill

    async def backfill_monitors(self, session_id: str | None = None) -> None:
        """Re-fetch content each active monitor missed (reconnect / session restore). Matches ONLY
        the monitor being filled; the per-monitor high water (not the ring) is the idempotence."""
        if self.monitor_eligibility() is not None:
            return
        adapter = self._manager.adapter_for(self.relay_id)
        now = self._now()
        for m in list(self.store.monitors(session_id)):
            if m["status"] != ACTIVE or m.get("paused"):
                continue
            try:
                await self._backfill_monitor(adapter, m, now)
            except RelayError as e:
                self.store.touch_monitor(m["id"], last_error=safe_error_code(e.code)[:60])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Monitor backfill failed")

    async def _backfill_monitor(self, adapter, m: dict[str, Any], now: float) -> None:
        mid = m["id"]
        durable = m.get("last_seen_ts") or self._ts_str(now)
        if mid in self._hold:  # a failed / lost / cancelled envelope: re-collect from the durable cursor
            self._high_water[mid] = durable
            self._recent_keys.pop(mid, None)
        self._hold.discard(mid)  # this pass re-collects everything after the durable cursor
        high = self._high_water.get(mid) or durable
        floor = now - const.MONITOR_BACKFILL_MAX_AGE_SECONDS
        older_omitted = _tsf(durable) < floor
        oldest = self._ts_str(max(_tsf(high), floor))
        messages = []
        page_cursor: str | None = None
        truncated = False
        for page_no in range(const.BACKFILL_MAX_PAGES):
            if m["kind"] == const.KIND_MONITOR_THREAD:
                page = await adapter.read_thread(
                    m["channel"], m["thread_ts"], const.BACKFILL_PAGE_SIZE, page_cursor, oldest=oldest
                )
            else:
                page = await adapter.read_history(
                    m["channel"], limit=const.BACKFILL_PAGE_SIZE, oldest=oldest, cursor=page_cursor
                )
            messages.extend(page.messages)
            page_cursor = page.next_cursor
            if not page_cursor:
                break
            truncated = page_no == const.BACKFILL_MAX_PAGES - 1
        if truncated or older_omitted:
            older_omitted = True
            await self._rec(
                event="monitor_backfill", outcome="truncated",
                code="backfill_truncated", session_id=m["session_id"], monitor_id=mid,
                monitor_kind=m["kind"], channel_id=m["channel"], count=len(messages),
            )
        ctx = self._match_context()
        for msg in sorted(messages, key=lambda x: _tsf(x.ts)):
            if _tsf(msg.ts) <= _tsf(self._high_water.get(mid) or high):
                continue
            event = event_from_message(self.relay_id, msg, m["channel"], channel_type=None)
            if isinstance(event, Drop):
                continue
            if event.thread_ts is None and m["kind"] == const.KIND_MONITOR_THREAD:
                event.thread_ts = m["thread_ts"]
            if never_content(event, ctx) or not matches_monitor(event, m):
                continue
            self.coalescer.add(event, [m], older_omitted=older_omitted)  # per-monitor key dedupe
            older_omitted = False


# Suspensions that also cancel envelopes of the monitor that are queued but not yet dispatched.
_CANCELLING_REASONS = frozenset({
    const.SUSPEND_CHANNEL_BLOCKED, const.SUSPEND_POLICY_UNSET, const.SUSPEND_SESSION_INACTIVE,
})

# Ignore reasons of events that DID pass the instruction trust rule (owner, self-DM, no agent marker):
# these are remembered in the dedupe ring (3a behavior); everything else is not (stage 3b-i, M2).
_CANDIDATE_IGNORES = frozenset({"no_handle", "unowned_thread", "empty"})


def _tsf(ts: str | None) -> float:
    try:
        return float(ts or 0)
    except ValueError:
        return 0.0
