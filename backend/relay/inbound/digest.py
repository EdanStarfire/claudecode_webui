"""Scheduled digest monitors (issue #2103 stage 3c-i): the scheduler loop and the run.

A digest is a ``mode="digest"`` monitor of one of the four kinds: it never matches live events and
is never backfilled on ``hello``; this scheduler is its only reader. At each slot (``next_run``) ONE
bounded run collects what happened since the durable cursor (at most 72 h) with the existing
backfill strategies, and enqueues ONE envelope only when there is content (an empty period never
wakes a session). Slots missed while the Backend was down merge into one late run; a slot is never
run early. Cursor-on-confirm, policy re-check, consent and fencing are those of live monitors.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

from ..adapter import RelayError
from . import constants as const
from . import content as monitor_content
from . import unread
from .backfill import BackfillRun, backfill_dms, backfill_mentions, collect_thread_or_channel
from .coalescer import BufferedMessage
from .health import iso
from .schedule import next_run_with_jitter

if TYPE_CHECKING:
    from .service import InboundService

logger = logging.getLogger(__name__)

SOURCE_BY_TRIGGER = {"schedule": "schedule", "late": "late", "agent": "agent", "manual": "owner"}
_GAP_KEYS = frozenset({
    "hydration_cap", "hydration_failed", "conversation_cap", "rate_limited", "truncated",
    "list_failed", "search_failed", "conversation_failed", "fetch_failed", "error", "time_cap",
})
# unread reasons that are problems worth the 5-minute drop summary (the rest are routine)
_UNREAD_DROP_REASONS = ("blocked_channel", "policy_unset", "conversation_cap", "time_cap", "conversation_failed")


def _tsf(ts: str | None) -> float:
    try:
        return float(ts or 0)
    except ValueError:
        return 0.0


class RunNowRefusedError(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


class DigestScheduler:
    def __init__(self, svc: InboundService) -> None:
        self._svc = svc
        self._lock = asyncio.Lock()  # one run at a time, relay-wide (scheduled and run-now alike)
        self.running: set[str] = set()  # monitor ids queued for / in a run
        self.not_before = 0.0
        self._task: asyncio.Task | None = None
        self._notices: dict[str, list[dict[str, Any]]] = {}  # session -> incomplete runs not yet announced

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self.not_before = self._svc._now() + const.DIGEST_STARTUP_DELAY_SECONDS
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._loop(), name=f"inbound-digest-{self._svc.relay_id}"
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await self._svc._digest_sleep(const.DIGEST_TICK_SECONDS)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Digest scheduler tick failed")

    # --------------------------------------------------------------------- tick

    async def tick(self) -> int:
        """Run the digests that are due (never early; oldest slot first; at most 3 per tick). Returns
        how many were started."""
        svc = self._svc
        now = svc._now()
        if svc._stopping or now < self.not_before:
            return 0
        ran = 0
        for rec in svc.store.due_digests(now)[: const.DIGEST_MAX_RUNS_PER_TICK]:
            async with self._lock:
                m = svc.store.get_monitor(rec["id"])
                now = svc._now()
                if m is None or m.get("next_run") is None or float(m["next_run"]) > now or m["id"] in self.running:
                    continue
                slot = float(m["next_run"])
                late = now - slot > 2 * const.DIGEST_TICK_SECONDS
                # the next slot is persisted BEFORE the run: a crash mid-run never re-runs this slot
                await svc.store.set_next_run(m["id"], next_run_with_jitter(m["schedule"], m["id"], now))
                self.running.add(m["id"])
                try:
                    await self._run(m, trigger="late" if late else "schedule", regular=True)
                finally:
                    self.running.discard(m["id"])
            ran += 1
        return ran

    # ------------------------------------------------------------------ run now

    def request_run_now(self, session_id: str | None, monitor_id: str, *, trigger: str) -> dict[str, Any]:
        """Validate and start an immediate run (does not wait for it, never touches ``next_run``).
        Raises ``RunNowRefusedError``."""
        svc = self._svc
        m = svc.store.get_monitor(monitor_id)
        if m is None or (session_id is not None and m.get("session_id") != session_id):
            raise RunNowRefusedError("monitor_not_found", "No such monitor of this session.")
        if m.get("mode") != const.MODE_DIGEST:
            raise RunNowRefusedError("not_a_digest_monitor", "Only digest monitors (with a schedule) can be run now.")
        if m.get("paused"):
            raise RunNowRefusedError("monitor_paused", "The monitor is paused by the owner.")
        if m.get("status") != "active":
            raise RunNowRefusedError(
                "monitor_suspended", f"The monitor is suspended ({m.get('suspended_reason') or 'unknown'}).",
            )
        problem = svc.digest_eligibility()
        if problem:
            raise RunNowRefusedError("inbound_unavailable", f"Digests are unavailable on this relay ({problem}).")
        if monitor_id in self.running:
            raise RunNowRefusedError("run_in_progress", "A run of this monitor is already in progress.")
        minimum = const.RUN_NOW_AGENT_MIN_SECONDS if trigger == "agent" else const.RUN_NOW_OWNER_MIN_SECONDS
        now = svc._now()
        last = float(m.get("last_manual_run_at") or 0)
        if now - last < minimum:
            left = int(minimum - (now - last)) + 1
            raise RunNowRefusedError(
                "run_too_soon", f"Run now is limited to once every {int(minimum // 60) or 1} min "
                f"({left} s left).", seconds_left=left,
            )
        svc.store.touch_monitor(monitor_id, last_manual_run_at=now)
        self.running.add(monitor_id)
        svc._spawn(self._run_now(monitor_id, trigger))
        return {"started": True}

    async def _run_now(self, monitor_id: str, trigger: str) -> None:
        svc = self._svc
        try:
            async with self._lock:
                m = svc.store.get_monitor(monitor_id)
                if m is not None:
                    await self._run(m, trigger=trigger, regular=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Digest run-now failed")
        finally:
            self.running.discard(monitor_id)

    # ---------------------------------------------------------------------- run

    async def _audit(self, m: dict[str, Any], trigger: str, outcome: str, **fields: Any) -> None:
        await self._svc._rec(
            event="monitor_digest", outcome=outcome, session_id=m["session_id"], monitor_id=m["id"],
            monitor_kind=m["kind"], channel_id=m.get("channel"), source=SOURCE_BY_TRIGGER[trigger], **fields,
        )

    def _last_run(self, outcome: str, *, count: int = 0, omitted: int = 0, gap: bool = False,
                  late: bool = False, trigger: str | None = None, code: str | None = None,
                  retry: int | None = None, retry_at: float | None = None) -> dict[str, Any]:
        run: dict[str, Any] = {"at": self._svc._now(), "outcome": outcome, "messages": count,
                               "omitted": omitted, "gap": gap, "late": late, "trigger": trigger}
        if code:
            run["code"] = code
        if retry is not None:
            run["retry"] = retry
        if retry_at is not None:
            run["retry_at"] = retry_at
        return run

    async def _finish(
        self, m: dict[str, Any], trigger: str, outcome: str, *, code: str | None = None,
        count: int = 0, omitted: int = 0, reason_counts: dict[str, int] | None = None,
        envelope_id: str | None = None, retry: int | None = None, gap: bool = False,
        retry_at: float | None = None, **fields: Any,
    ) -> None:
        svc = self._svc
        updates: dict[str, Any] = {"last_run_trigger": trigger, **fields}
        if outcome != "delivered":  # a delivered run is recorded when the session confirms it
            updates["last_run"] = self._last_run(
                outcome, count=count, omitted=omitted, gap=gap, late=trigger == "late", trigger=trigger,
                code=code, retry=retry, retry_at=retry_at,
            )
            updates["last_run_at"] = svc._now()
        if outcome == "empty":  # a clean run ends a streak of failures
            updates["run_failures"] = 0
        svc.store.touch_monitor(m["id"], **updates)
        svc._monitors_changed(m["session_id"])
        await self._audit(
            m, trigger, outcome, code=code or ("late" if trigger == "late" else None), count=count,
            omitted=omitted, reason_counts=reason_counts or None, envelope_id=envelope_id, retry=retry,
            retry_at=iso(retry_at),
        )

    async def _run(self, m: dict[str, Any], *, trigger: str, regular: bool) -> None:
        svc = self._svc
        mid, session_id = m["id"], m["session_id"]
        now = svc._now()
        problem = svc.digest_eligibility()
        if problem:
            await self._finish(m, trigger, "refused", code=problem)
            return
        valid, policies = await svc.apply_policy(session_id, resume=False, only={mid})
        if mid not in valid:
            rec = svc.store.get_monitor(mid) or m
            code = rec.get("suspended_reason") or ("paused" if rec.get("paused") else "not_active")
            await self._finish(m, trigger, "refused", code=code)
            return
        m = svc.store.get_monitor(mid) or m
        if regular and svc.port.pending_monitor_count(session_id, svc.relay_id) >= const.MAX_PENDING_ENVELOPES:
            defers = int(m.get("slot_defers") or 0)
            if defers < const.DIGEST_DEFER_MAX:
                await svc.store.set_next_run(
                    mid, now + const.DIGEST_DEFER_STEP_SECONDS, slot_defers=defers + 1,
                )
                await self._finish(m, trigger, "deferred", code="session_busy", retry=defers + 1,
                                   retry_at=now + const.DIGEST_DEFER_STEP_SECONDS)
                return
        if int(m.get("slot_defers") or 0):
            svc.store.touch_monitor(mid, slot_defers=0)

        if m["kind"] == const.KIND_MONITOR_UNREAD:
            await self._run_unread(m, trigger, regular, now, policies)
            return

        durable = _tsf(m.get("last_seen_ts")) or now
        since = max(durable, now - const.DIGEST_WINDOW_MAX_AGE_SECONDS)
        older_omitted = durable < now - const.DIGEST_WINDOW_MAX_AGE_SECONDS
        svc._reset_hold(m)  # a failed / cancelled earlier envelope: re-collect from the durable cursor
        collected: dict[str, Any] = {}

        def sink(event, monitor, _older) -> None:
            key = f"{event.channel}:{event.ts}"
            if not svc.monitor_key_seen(mid, key):
                collected.setdefault(key, event)

        run = BackfillRun(
            now, digest=True, sink=sink, hydration_cap=const.DIGEST_HYDRATION_CAP,
            max_age=const.DIGEST_WINDOW_MAX_AGE_SECONDS, pacer=svc.api_limiter.wait,
            list_cache=svc.list_cache, info_cache=svc.info_cache,
        )
        adapter = svc._manager.adapter_for(svc.relay_id)
        try:
            if m["kind"] == const.KIND_MONITOR_DMS:
                await backfill_dms(svc, adapter, [m], run)
            elif m["kind"] == const.KIND_MONITOR_MENTIONS:
                await backfill_mentions(svc, adapter, [m], run)
            else:
                await collect_thread_or_channel(svc, adapter, m, run)
        except asyncio.CancelledError:
            raise
        except RelayError as e:
            run.counts["rate_limited" if e.code == "ratelimited" else "error"] += 1
            run.note_gap(since)
        except Exception:
            logger.exception("Digest collection failed")
            run.counts["error"] += 1
            run.note_gap(since)
        reasons = dict(run.counts)
        for reason, n in run.counts.items():
            svc._drops[f"monitor_{reason}"] += n

        if run.counts.get("rate_limited"):
            await self._rate_limited(m, trigger, regular, since, now, reasons)
            return
        if run.counts.get("error") or run.counts.get("fetch_failed"):
            failures = int(m.get("run_failures") or 0) + 1
            await self._finish(m, trigger, "failed", code="collect_failed", reason_counts=reasons,
                               run_failures=failures)
            return
        svc.store.touch_monitor(mid, slot_retries=0)

        gap_counts = {k: v for k, v in reasons.items() if k in _GAP_KEYS and v}
        blocked = {c for c, eff in policies.items() if eff is not None and eff.policy == "blocked"}
        events = sorted(collected.values(), key=lambda e: _tsf(e.ts))
        kept: list[BufferedMessage] = []
        cover_ts = 0.0
        for event in events:
            cover_ts = max(cover_ts, _tsf(event.ts))
            svc.note_monitor_key(mid, f"{event.channel}:{event.ts}")
            if m["kind"] in const.SCOPE_KINDS and event.channel in blocked:
                svc._drop("monitor_blocked_channel")  # an explicitly blocked channel stays excluded
                svc.store.bump_counters(mid, dropped=1)
                reasons["blocked_channel"] = reasons.get("blocked_channel", 0) + 1
                continue
            kept.append(BufferedMessage(event, [mid]))
        included, omitted = monitor_content.select_for_envelope(kept)
        omitted_by_channel: Counter = Counter(e.event.channel for e in omitted)
        omitted_total = len(omitted)
        if gap_counts:
            self._notices.setdefault(session_id, []).append(
                {"counts": gap_counts, "from": run.gap_from if run.gap_from is not None else since, "to": now}
            )

        if not included:
            svc._advance_unqueued_cover({mid: svc._ts_str(max(now - const.BACKFILL_MARGIN_SECONDS, 0))})
            await self._finish(m, trigger, "empty", reason_counts=reasons, omitted=0, gap=bool(gap_counts))
            return

        notice = self._take_notice(session_id)
        cover = {mid: svc._ts_str(cover_ts)}
        digest_run = {
            "at": now, "messages": len(included), "omitted": omitted_total, "gap": bool(notice),
            "late": trigger == "late", "trigger": trigger,
        }
        envelope_id = await svc._enqueue_envelope(
            session_id, included, omitted_by_channel, cover, kind="digest", older_omitted=older_omitted,
            since=since, notice=notice, watch_info={"digest_run": digest_run},
        )
        if envelope_id is None:
            if notice:  # the announcement was not delivered: keep it for the next envelope
                self._notices.setdefault(session_id, []).append(
                    {"counts": gap_counts, "from": since, "to": now}
                )
            await self._finish(m, trigger, "failed", code="enqueue_failed", reason_counts=reasons,
                               count=len(included))
            return
        await self._finish(
            m, trigger, "delivered", count=len(included), omitted=omitted_total,
            reason_counts=reasons, envelope_id=envelope_id, gap=bool(notice),
        )

    async def _run_unread(
        self, m: dict[str, Any], trigger: str, regular: bool, now: float, policies: dict[str, Any]
    ) -> None:
        """One unread run (3c-ii): evaluate Slack's read state, enqueue ONE envelope only when
        something is newly unread. Rate limits follow the digest rules; ``notified`` advances only
        when the session confirms the delivery (``InboundService._monitor_outcome``)."""
        svc = self._svc
        mid, session_id = m["id"], m["session_id"]
        since = float(m.get("last_run_at") or now - 3600)
        adapter = svc._manager.adapter_for(svc.relay_id)
        try:
            res = await unread.evaluate(svc, adapter, m, policies)
        except asyncio.CancelledError:
            raise
        except RelayError as e:
            res = unread.UnreadResult(rate_limited=e.code == "ratelimited", failed=e.code != "ratelimited")
            res.counts["rate_limited" if res.rate_limited else "error"] += 1
        except Exception:
            logger.exception("Unread evaluation failed")
            res = unread.UnreadResult(failed=True)
            res.counts["error"] += 1
        reasons = dict(res.counts)
        for reason in _UNREAD_DROP_REASONS:
            if res.counts.get(reason):
                svc._drops[f"monitor_{reason}"] += res.counts[reason]
        if res.rate_limited:
            await self._rate_limited(m, trigger, regular, since, now, reasons)
            return
        if res.failed:
            await self._finish(m, trigger, "failed", code="collect_failed", reason_counts=reasons,
                               run_failures=int(m.get("run_failures") or 0) + 1)
            return
        svc.store.touch_monitor(mid, slot_retries=0)
        if res.consumed_empty:  # evaluated, nothing to tell the session: not looked at again until newer
            svc.store.set_notified(mid, res.consumed_empty)
        gap_counts = {k: v for k, v in reasons.items() if k in _GAP_KEYS and v}
        if gap_counts:
            self._notices.setdefault(session_id, []).append({"counts": gap_counts, "from": since, "to": now})
        if not res.conversations:
            svc._advance_unqueued_cover({mid: svc._ts_str(now)})
            await self._finish(m, trigger, "empty", reason_counts=reasons, gap=bool(gap_counts))
            return
        notice = self._take_notice(session_id)
        count = len(res.conversations)
        digest_run = {
            "at": now, "messages": count, "conversations": count, "omitted": res.omitted,
            "gap": bool(notice), "late": trigger == "late", "trigger": trigger,
        }
        envelope_id = await svc.enqueue_unread(
            m, res.conversations, as_of=now, omitted=res.omitted, cover={mid: svc._ts_str(now)},
            notice=notice, watch_info={"digest_run": digest_run},
        )
        if envelope_id is None:
            if notice:  # the announcement was not delivered: keep it for the next envelope
                self._notices.setdefault(session_id, []).append({"counts": gap_counts, "from": since, "to": now})
            await self._finish(m, trigger, "failed", code="enqueue_failed", reason_counts=reasons, count=count)
            return
        await self._finish(
            m, trigger, "delivered", count=count, omitted=res.omitted, reason_counts=reasons,
            envelope_id=envelope_id, gap=bool(notice),
        )

    def _take_notice(self, session_id: str) -> str | None:
        pending = self._notices.pop(session_id, None)
        if not pending:
            return None
        counts: Counter = Counter()
        for p in pending:
            counts.update(p["counts"])
        period = (min(p["from"] for p in pending), max(p["to"] for p in pending))
        return monitor_content.digest_gap_line(dict(counts), period)

    async def _rate_limited(
        self, m: dict[str, Any], trigger: str, regular: bool, since: float, now: float,
        reasons: dict[str, int],
    ) -> None:
        """D9: stop the run; a scheduled slot is retried 5 min later up to 3 times, then given up
        (next regular slot, gap notice). Every reschedule is audited; no retry storm."""
        svc = self._svc
        mid = m["id"]
        failures = int(m.get("run_failures") or 0) + 1
        if not regular:  # run-now: recorded, the schedule is untouched
            await self._finish(m, trigger, "rate_limited", code="rate_limited", reason_counts=reasons,
                               run_failures=failures)
            return
        retries = int(m.get("slot_retries") or 0) + 1
        if retries <= const.DIGEST_RATE_LIMIT_MAX_RETRIES:
            await svc.store.set_next_run(
                mid, now + const.DIGEST_RATE_LIMIT_RESCHEDULE_SECONDS, slot_retries=retries,
                run_failures=failures,
            )
            await self._finish(m, trigger, "rate_limited", code="rate_limited_rescheduled",
                               retry=retries, reason_counts=reasons,
                               retry_at=now + const.DIGEST_RATE_LIMIT_RESCHEDULE_SECONDS)
            return
        await svc.store.set_next_run(
            mid, next_run_with_jitter(m["schedule"], mid, now), slot_retries=0, run_failures=failures,
        )
        self._notices.setdefault(m["session_id"], []).append(
            {"counts": {"rate_limited": 1}, "from": since, "to": now}
        )
        await self._finish(m, trigger, "rate_limited", code="rate_limited_gave_up",
                           retry=const.DIGEST_RATE_LIMIT_MAX_RETRIES, reason_counts=reasons, gap=True)
