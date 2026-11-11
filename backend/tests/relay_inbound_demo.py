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
  3. creates monitors of every kind and state for the sessions found under ``<data-dir>/sessions``.
No Slack token is read or needed. After the run, start the app: the endpoints read the seeded files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
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
    s1, s2, s3 = (sessions[i % len(sessions)][0] for i in range(3))

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
    # a connection blip near the end: genuine status changes through the service
    env.service._set_status(const.STATUS_BACKOFF, "transport_error")
    await advance(env, 90)
    env.service._set_status(const.STATUS_CONNECTED, None)
    await advance(env, 30)
    await settle(env)
    _ = (thread, channel, users, expiring)
    await env.service.stop()


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
    parser.add_argument("--user-secret", default="demo-user-token")
    parser.add_argument("--app-secret", default="demo-app-token")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve()
    sessions = find_sessions(data_dir)
    if not sessions:
        raise SystemExit(f"no sessions under {data_dir / 'sessions'}: create 2-3 in the test instance first")
    patch_relay_record(data_dir, args.relay_id, args.user_secret, args.app_secret)
    for name in ("audit.jsonl", "inbound_state.json", "subscriptions.json"):
        (data_dir / "relays" / args.relay_id / name).unlink(missing_ok=True)
    asyncio.run(drive(data_dir, args.relay_id, sessions, time.time()))
    print(f"seeded {data_dir / 'relays' / args.relay_id}")


if __name__ == "__main__":
    main()
