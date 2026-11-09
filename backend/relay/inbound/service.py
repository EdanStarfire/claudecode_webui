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
import logging
import random
import time
import uuid
from collections import Counter
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
from .dispatch import format_instruction, instruction_summary
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
        self._rate_audit_at = -1e9
        self.status = const.STATUS_STOPPED
        self.detail: str | None = None
        self.connected_at: float | None = None

    # ------------------------------------------------------------- lifecycle

    @property
    def record(self):
        return self._manager.relays.get(self.relay_id)

    async def start(self) -> None:
        self._stopping = False
        self._stop.clear()
        self.state.load()
        self.store.load()
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

    async def stop(self) -> None:
        self._stopping = True
        self._stop.set()
        tasks = list(self._tasks)
        if self._backfill_task is not None:
            tasks.append(self._backfill_task)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        self._backfill_task = None
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                await transport.disconnect()
            except Exception:
                logger.debug("Inbound transport disconnect failed", exc_info=True)
        await self._flush_drop_summary()
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

    def _spawn(self, coro) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)

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
        """Dedupe + classify + persist-before-ack. True when queued as an instruction/refusal."""
        if self.state.seen(event.channel, event.ts):
            self._drop("deduped")
            return False
        self.state.mark_seen(event.channel, event.ts)
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
                self._drop(decision.reason or "ignored")
                self.state.schedule_flush()
                return False
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
            await self._audit_instruction(event, d, "refused", "delivery_failed")
            await self._reply_once(entry, event, thread_ts, "delivery_failed")
            return
        entry["delivered"] = True  # persisted: a re-run never enqueues this instruction again
        await self.state.flush()
        if record is not None and record.get("id"):
            await self.store.touch_thread(
                record["id"], last_seen_ts=event.ts if event.ts != thread_ts else None
            )
        await self._audit_instruction(
            event, d, "deduped" if outcome.status == "already_delivered" else "delivered"
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
        await self.store.remove_by_session(session_id)
        for key, entry in list(self.state.inflight.items()):
            if (entry.get("decision") or {}).get("session_id") == session_id:
                self.state.inflight.pop(key, None)
        await self.state.flush()

    async def on_session_disposed(self, session_id: str) -> None:
        await self.store.set_status_by_session(session_id, SUSPENDED, reason="disposed")

    async def on_session_started(self, session_id: str) -> None:
        await self.store.set_status_by_session(session_id, ACTIVE, only_reason="disposed")

    def has_thread_grant(self, session_id: str, channel: str, thread_ts: str) -> bool:
        record = self.record
        if record is None or not record.inbound_enabled:
            return False
        return self.store.has_active_thread(session_id, channel, thread_ts)


def _tsf(ts: str | None) -> float:
    try:
        return float(ts or 0)
    except ValueError:
        return 0.0
