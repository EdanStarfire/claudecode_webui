"""TEST-ONLY seeding driver for the Library > Relays > Inbound tab (issue #2103 stage 3d-i).

Not collected by pytest (no ``test_`` prefix). Run with the app STOPPED::

    uv run python backend/tests/relay_inbound_demo.py --data-dir test_data_3d --relay-id <id>

What it does, writing only inside ``--data-dir``:
  1. points the relay record (``relays/<slug>.json``) at demo secret NAMES and sets owner / self-DM;
  2. drives a REAL ``InboundService`` against an in-process fake transport / adapter / delivery port
     (the fakes of ``relay_inbound_support``; nothing is added to production code) with an injected
     clock that starts 23 h ago, so ``audit.jsonl`` / ``inbound_state.json`` / ``subscriptions.json``
     hold genuine rows spread over the last day: connection changes, instructions (delivered, refused,
     late, rate limited, delivery failed), monitor deliveries, a flood, delivery failures, suspensions
     and a backfill gap with cap reasons;
  3. (3c-iii) scheduled digest / unread monitors in every state with their audit rows;
  4. creates monitors of every kind and state for the sessions found under ``<data-dir>/sessions``.
  4. (3d-ii) appends genuine user-message records to the first sessions' ``messages.jsonl``: an owner
     instruction, a multi-line instruction with markup, and a monitor envelope, each produced by a real
     ``InboundService`` and delivered through the REAL ``DeliveryGate`` / ``PendingStore`` against a mock
     coordinator, then stored with ``MessageRecord.from_user_input`` (the production record builder), so the
     timeline shows real ``relay_inbound`` metadata. The sessions' relay summary comes from the app itself.
No Slack token is read or needed. After the run, start the app: the endpoints read the seeded files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import types
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.relay.audit import RelayAuditLog  # noqa: E402
from backend.relay.inbound import constants as const  # noqa: E402
from backend.tests import relay_inbound_support as support  # noqa: E402
from backend.tests.relay_inbound_support import (  # noqa: E402
    OTHER_USER,
    OWNER,
    SELF_DM,
    audit_events,
    envelope,
    make_inbound,
    message_payload,
    settle,
    started,
)
from backend.tests.relay_monitor_support import emit, fire_window, monitor  # noqa: E402

CHANNELS = ["C0DEMO0001", "C0DEMO0002", "C0DEMO0003", "C0DEMO0004"]
HOURS = 3600.0


class ClockAudit(RelayAuditLog):
    """Audit log that stamps rows with the injected clock (spreads the demo over 24 h)."""

    def __init__(self, data_dir, clock):
        super().__init__(data_dir)
        self._clock = clock

    async def record(self, relay_id, **fields):
        fields.setdefault("ts", datetime.fromtimestamp(self._clock.now(), UTC).isoformat())
        await super().record(relay_id, **fields)


def find_sessions(data_dir: Path) -> list[tuple[str, str, str | None]]:
    out = []
    for state in sorted((data_dir / "sessions").glob("*/state.json")):
        try:
            data = json.loads(state.read_text())
        except ValueError:
            continue
        sid = data.get("session_id") or state.parent.name
        out.append((sid, data.get("name") or sid, (data.get("config") or {}).get("relay_handle")))
    return out


def patch_relay_record(data_dir: Path, relay_id: str, user_secret: str, app_secret: str) -> None:
    for path in (data_dir / "relays").glob("*.json"):
        data = json.loads(path.read_text())
        if data.get("relay_id") != relay_id:
            continue
        data.update(
            user_token_secret=user_secret, app_token_secret=app_secret, owner_user_id=OWNER,
            self_dm_channel_id=SELF_DM, workspace_id=support.TEAM, workspace_name="Demo workspace",
            workspace_url="https://demo.slack.com/", inbound_enabled=True,
        )
        path.write_text(json.dumps(data, indent=2))
        return
    raise SystemExit(f"relay {relay_id} not found under {data_dir / 'relays'} (create it in the UI first)")


async def advance(env, seconds: float) -> None:
    env.clock.advance(seconds)
    await asyncio.sleep(0.01)


async def instruction(env, text: str, seq: int, *, age: float = 0.0) -> None:
    ts = env.clock.ts(offset=-age, seq=seq)
    await env.t.emit(envelope(message_payload(text, ts, channel=SELF_DM, user=OWNER), f"I{seq}"))
    await settle(env)


async def drive(data_dir: Path, relay_id: str, sessions, real_now: float) -> None:
    support.RELAY_ID = relay_id  # the fakes build their ids from this module constant
    clock = support.FakeClock(real_now - 23 * HOURS)
    env = make_inbound(data_dir, handles={}, clock=clock)
    audit = ClockAudit(data_dir, clock)
    env.audit = audit
    env.service._audit = audit
    env.sessions.infos.clear()
    for i, (sid, name, handle) in enumerate(sessions):
        handle = handle or f"demo-{i + 1}"
        env.sessions.add(sid, relay_id=relay_id)
        env.sessions.infos[sid].name = name
        env.sessions.infos[sid].config["relay_handle"] = handle
        env.index.rebind(sid, handle)
    handles = [env.sessions.infos[s[0]].config["relay_handle"] for s in sessions]
    for channel in CHANNELS:
        env.relay_service.policies[channel] = "read_only"
    # 3d-ii: with >= 4 sessions the first two hold the monitors (lit: attention-level states), the third only
    # an owner-caused suspension (monitors, but quiet) and the fourth none (instruction-only)
    many = len(sessions) >= 4
    s1, s2, s3 = (sessions[i % len(sessions)][0] for i in range(3))
    if many:
        s3 = s1
    quiet_session = sessions[2][0] if many else None

    env.t = await started(env)  # connecting -> connected rows
    await advance(env, 600)

    # ---- instructions over the day
    await instruction(env, f"!{handles[0]} run the nightly report", 1)  # delivered (+ received reaction)
    await instruction(env, f"!{handles[0]} this one fails later", 21)
    failed = env.sink.calls[-1]
    env.port.set_status(failed["comm_id"], "failed", session_id=failed["session_id"])
    await env.service.watcher.poll_once()  # instruction delivery_failed + one marked reply
    await advance(env, 900)
    await instruction(env, "!nobody do something", 2)  # refused: unknown handle
    await advance(env, 900)
    await instruction(env, f"!{handles[0]} too old to run", 3, age=1500)  # late (older than 15 min)
    await advance(env, 900)
    for n in range(4, 20):  # burst over the relay limit: rate_limited
        await instruction(env, f"!{handles[0]} burst {n}", n)
    await advance(env, 1800)

    # ---- monitors of every kind
    thread = await monitor(env, kind=const.KIND_MONITOR_THREAD, channel=CHANNELS[0],
                           thread_ts=env.clock.ts(offset=-3600), keywords=(), session=s1)
    channel = await monitor(env, channel=CHANNELS[0], keywords=("deploy", "release", "hotfix"), session=s1)
    users = await monitor(env, channel=CHANNELS[1], keywords=("incident",), users=(OTHER_USER,), session=s2)
    mentions = await monitor(env, kind=const.KIND_MONITOR_MENTIONS, channel=None, keywords=(), session=s2)
    dms = await monitor(env, kind=const.KIND_MONITOR_DMS, channel=None, keywords=(), session=s3)
    expiring = await monitor(env, channel=CHANNELS[2], keywords=("sev1",), session=s3,
                             expires_at=real_now + 12 * HOURS)
    flooded = await monitor(env, channel=CHANNELS[3], keywords=("noise",), session=s2)
    failing = await monitor(env, channel=CHANNELS[1], keywords=("deploy",), session=s3)
    blocked = await monitor(env, channel=CHANNELS[2], keywords=("billing",), session=s1)
    unset = await monitor(env, channel=CHANNELS[3], keywords=("audit",), session=s1)
    disposed = await monitor(env, channel=CHANNELS[0], keywords=("rollback",), session=s2)
    inactive = await monitor(env, channel=CHANNELS[1], keywords=("handover",), session=s3)
    paused = await monitor(env, channel=CHANNELS[0], keywords=("standup",), session=s1)
    quiet = (
        await monitor(env, channel=CHANNELS[2], keywords=("billing",), session=quiet_session)
        if quiet_session else None
    )

    # ---- deliveries (real coalescer + watcher); one monitor keeps failing
    for seq, text in enumerate(["deploy finished", "release 1.2 is out", "hotfix merged"], start=100):
        await emit(env, env.t, text, seq=seq, channel=CHANNELS[0])
        await advance(env, 5)
    await fire_window(env)
    for call in env.port.calls:
        env.port.set_status(call["comm_id"], "delivered", session_id=call["session_id"])
    await env.service.watcher.poll_once()
    await advance(env, 2 * HOURS)

    await emit(env, env.t, "deploy broke", seq=200, channel=CHANNELS[1])
    await fire_window(env)
    env.port.set_status(env.port.calls[-1]["comm_id"], "failed", session_id=env.port.calls[-1]["session_id"])
    await env.service.watcher.poll_once()  # failures=1 + last_error on the monitors of that envelope
    await advance(env, 3 * HOURS)

    for i in range(125):  # over the per-monitor flood limit: monitor_flood audit + dropped counter
        await emit(env, env.t, f"noise {i}", seq=300 + i, channel=CHANNELS[3])
    await fire_window(env)
    await advance(env, HOURS)

    # ---- backfill gap with cap reasons (real _finish_scope_run)
    run = support_run(env, real_now)
    await env.service._finish_scope_run(run, [mentions, dms], False)
    await advance(env, HOURS)

    # ---- owner / system state changes (real service methods)
    await env.service.set_paused(paused["id"], True, source="owner")
    for rec, reason in (
        (flooded, const.SUSPEND_FLOOD), (failing, const.SUSPEND_DELIVERY_FAILED),
        (blocked, const.SUSPEND_CHANNEL_BLOCKED), (unset, const.SUSPEND_POLICY_UNSET),
        (disposed, const.SUSPEND_DISPOSED), (inactive, const.SUSPEND_SESSION_INACTIVE),
    ):
        await env.service._suspend(env.service.store.get_monitor(rec["id"]), reason)
        await advance(env, 60)
    if quiet is not None:
        await env.service._suspend(env.service.store.get_monitor(quiet["id"]), const.SUSPEND_CHANNEL_BLOCKED)
    # a connection blip near the end: genuine status changes through the service
    env.service._set_status(const.STATUS_BACKOFF, "transport_error")
    await advance(env, 90)
    env.service._set_status(const.STATUS_CONNECTED, None)
    await advance(env, 30)
    await settle(env)
    _ = (thread, channel, users, expiring)
    await seed_scheduled(env, real_now, (s1, s2, sessions[3][0] if many else s3))
    await env.service.stop()


async def seed_scheduled(env, real_now: float, sessions: tuple[str, str, str]) -> None:
    """3c-iii: scheduled (digest / unread) monitors in every state with their ``last_run`` variants and the
    matching ``monitor_digest`` audit rows, through the real service / store (nothing is faked but Slack)."""
    from backend.relay.inbound import schedule as sched

    s1, s2, s3 = sessions
    store = env.service.store

    def parse(raw, tz=None):
        return sched.parse_schedule(raw, owner_tz=tz, now=real_now - 6 * HOURS)

    async def digest(kind, channel, session, raw, *, tz="Europe/Paris", keywords=(), thread_ts=None, options=None,
                     consent=None):
        rec, _ = await env.service.create_monitor(
            session, kind, channel, thread_ts=thread_ts, filters={"keywords": list(keywords), "users": []},
            expires_at=None, schedule=parse(raw, tz), options=options, consent=consent,
        )
        return rec

    consent = {"at": real_now - 5 * HOURS, "via": "prompt"}
    daily = {"daily": {"times": ["09:00", "17:00"], "days": ["mon", "tue", "wed", "thu", "fri"]}}
    d_chan = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[0], s1, daily, keywords=("deploy",))
    d_thread = await digest(const.KIND_MONITOR_THREAD, CHANNELS[1], s1, {"every_minutes": 120},
                            thread_ts=env.clock.ts(offset=-7200))
    d_mentions = await digest(const.KIND_MONITOR_MENTIONS, None, s2, {"every_minutes": 360}, consent=consent)
    d_dms = await digest(const.KIND_MONITOR_DMS, None, s3, {"daily": {"times": ["08:30"]}}, tz=None, consent=consent)
    unread = await digest(
        const.KIND_MONITOR_UNREAD, None, s2, {"every_minutes": 120},
        options={"dms": True, "channels": [CHANNELS[0]], "exclude": ["D0DEMOA", "D0DEMOB"], "mentions_only": True,
                 "repeat": False}, consent=consent)
    d_paused = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[2], s1, {"every_minutes": 240}, keywords=("billing",))
    d_blocked = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[3], s1, {"every_minutes": 480}, keywords=("audit",))
    d_failing = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[1], s2, {"every_minutes": 90}, keywords=("incident",))
    d_overdue = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[2], s3, {"every_minutes": 60}, keywords=("sev1",))
    d_deferred = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[3], s2, {"every_minutes": 180}, keywords=("noise",))
    d_late = await digest(const.KIND_MONITOR_CHANNEL, CHANNELS[0], s3, {"every_minutes": 720}, keywords=("rollback",))

    def last(at, outcome, **kw):
        return {"at": real_now - at, "outcome": outcome, "messages": 0, "omitted": 0, "gap": False,
                "late": False, "trigger": "schedule", **kw}

    async def state(rec, *, next_in, last_run=None, trigger="schedule", failures=0):
        await store.set_next_run(rec["id"], real_now + next_in, run_failures=failures)
        store.touch_monitor(rec["id"], last_run=last_run, last_run_at=last_run["at"] if last_run else None,
                            last_run_trigger=trigger if last_run else None)

    await state(d_chan, next_in=3 * HOURS + 120, last_run=last(2 * HOURS, "delivered", messages=12))
    await state(d_thread, next_in=40 * 60, last_run=last(80 * 60, "empty"))
    await state(d_mentions, next_in=5 * 60, last_run=last(300, "rate_limited", code="rate_limited_rescheduled",
                                                          retry=2, retry_at=real_now + 5 * 60))
    await state(d_dms, next_in=14 * HOURS, last_run=last(10 * HOURS, "rate_limited", code="rate_limited_gave_up", retry=3))
    await state(unread, next_in=75 * 60, last_run=last(45 * 60, "delivered", messages=3, trigger="manual"),
                trigger="manual")
    await state(d_paused, next_in=2 * HOURS, last_run=last(4 * HOURS, "delivered", messages=2))
    await state(d_blocked, next_in=3 * HOURS, last_run=last(HOURS, "refused", code="channel_blocked"))
    await state(d_failing, next_in=30 * 60, last_run=last(1800, "failed", code="collect_failed"), failures=3)
    await state(d_overdue, next_in=-30 * 60, last_run=last(90 * 60, "delivered", messages=1))
    await state(d_deferred, next_in=8 * 60, last_run=last(120, "deferred", code="session_busy", retry=1,
                                                         retry_at=real_now + 8 * 60))
    await state(d_late, next_in=6 * HOURS, last_run=last(3 * HOURS, "delivered", messages=4, late=True, trigger="late"),
                trigger="late")
    await env.service.set_paused(d_paused["id"], True, source="owner")
    await env.service._suspend(store.get_monitor(d_blocked["id"]), const.SUSPEND_CHANNEL_BLOCKED)

    # ---- audit rows of the runs (the 24 h counters / attention tags read these)
    async def run_row(rec, outcome, **fields):
        await env.service._rec(
            event="monitor_digest", outcome=outcome, session_id=rec["session_id"], monitor_id=rec["id"],
            monitor_kind=rec["kind"], source=fields.pop("source", "schedule"), **fields)
        await advance(env, 20)

    await run_row(d_chan, "delivered", count=12, omitted=0)
    await run_row(d_thread, "empty")
    await run_row(d_mentions, "rate_limited", code="rate_limited_rescheduled", retry=1)
    await run_row(d_mentions, "rate_limited", code="rate_limited_rescheduled", retry=2)
    await run_row(d_dms, "rate_limited", code="rate_limited_gave_up", retry=3)
    await run_row(unread, "accepted", code="run_now", source="owner")
    await run_row(unread, "delivered", count=3, source="owner")
    await run_row(d_late, "delivered", code="late", count=4, source="late")
    await run_row(d_deferred, "deferred", code="session_busy", retry=1)
    await run_row(d_blocked, "refused", code="channel_blocked")
    await run_row(d_paused, "refused", code="monitor_paused", source="owner")
    await run_row(d_failing, "failed", code="collect_failed")
    await run_row(d_overdue, "empty")


async def seed_timeline(data_dir: Path, relay_id: str, sessions) -> None:
    """Genuine instruction + monitor-envelope user messages through the real delivery gate (3d-ii)."""
    import tempfile
    from functools import partial
    from unittest.mock import AsyncMock, patch

    from backend.models.messages import MessageRecord
    from backend.relay.inbound.monitor_delivery import CoordinatorPort
    from backend.relay.inbound.sink import enqueue_external_comm
    from backend.tests.test_relay_inbound_integration import (
        _fast_sleep,
        _wait_for,
        live_coordinator,
    )

    support.RELAY_ID = relay_id
    plans = [
        (sessions[0], ["!my-agent run the nightly report",
                       "!my-agent summarise *this week*:\n- <@U0DEMO0002> asked about <https://example.com|the doc>\n- <b>not html</b> & `code`"],
         True),
        (sessions[min(1, len(sessions) - 1)], ["!my-agent check the staging deploy"], False),
    ]
    for (sid, _name, _handle), texts, with_monitor in plans:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "coord").mkdir()
            env = make_inbound(tmp)
            env.sessions.infos[support.SESSION].config["relay_id"] = relay_id
            env.relay_service.policies[CHANNELS[0]] = "read_only"
            coord, _info, sent = live_coordinator(tmp / "coord")
            port = CoordinatorPort(coord)
            env.service._sink = partial(enqueue_external_comm, coord)
            env.service.port = port
            env.service.watcher._probe = port
            env.t = await started(env)
            # only the delivery gate's own waits are shortened (a global asyncio.sleep patch would also
            # shorten the real-time waits the file writes need)
            shim = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio)})
            shim.sleep = AsyncMock(side_effect=_fast_sleep)
            with patch("backend.delivery_gate.asyncio", new=shim):
                for n, text in enumerate(texts, start=1):
                    ts = env.clock.ts(seq=n)
                    await env.t.emit(envelope(message_payload(text, ts, channel=SELF_DM, user=OWNER), f"T{n}"))
                    await settle(env)
                    assert await _wait_for(lambda n=n, sent=sent: len(sent) >= n), f"instruction was not delivered: {[(r['event'], r.get('code')) for r in audit_events(env)]}"
                if with_monitor:
                    await monitor(env, channel=CHANNELS[0], keywords=("deploy",))
                    await emit(env, env.t, "deploy finished, all green", seq=10, channel=CHANNELS[0])
                    await fire_window(env)
                    assert await _wait_for(lambda texts=texts, sent=sent: len(sent) > len(texts)), "envelope was not delivered"
            coord.delivery_gate.stop_when_idle("s1")
            await env.service.stop()
        path = data_dir / "sessions" / sid / "messages.jsonl"
        base = time.time() - 60 * (len(sent) + 1)
        with path.open("a") as fh:
            for i, (message, metadata, _inject) in enumerate(sent):
                record = MessageRecord.from_user_input(message, sid, metadata=metadata).to_dict()
                record["timestamp"] = base + 60 * i
                fh.write(json.dumps(record) + "\n")
        print(f"appended {len(sent)} relay_inbound messages to {path}")


def support_run(env, real_now):
    from backend.relay.inbound.backfill import BackfillRun

    run = BackfillRun(env.clock.now())
    run.counts.update({"non_member": 3, "hydration_cap": 2, "conversation_cap": 4, "truncated": 1})
    run.hydrations = 20
    run.note_gap(env.clock.now() - 1800)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--relay-id", required=True)
    parser.add_argument("--order", default="", help="comma separated session handles: monitors go to the first "
                        "two, a quiet (owner-suspended only) monitor to the third, the fourth gets none")
    parser.add_argument("--user-secret", default="demo-user-token")
    parser.add_argument("--app-secret", default="demo-app-token")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve()
    sessions = find_sessions(data_dir)
    if not sessions:
        raise SystemExit(f"no sessions under {data_dir / 'sessions'}: create 2-3 in the test instance first")
    if args.order:
        rank = {h: i for i, h in enumerate(args.order.split(","))}
        sessions.sort(key=lambda t: rank.get(t[2], len(rank)))
    patch_relay_record(data_dir, args.relay_id, args.user_secret, args.app_secret)
    for name in ("audit.jsonl", "inbound_state.json", "subscriptions.json"):
        (data_dir / "relays" / args.relay_id / name).unlink(missing_ok=True)
    asyncio.run(drive(data_dir, args.relay_id, sessions, time.time()))
    asyncio.run(seed_timeline(data_dir, args.relay_id, sessions))
    print(f"seeded {data_dir / 'relays' / args.relay_id}")


if __name__ == "__main__":
    main()
