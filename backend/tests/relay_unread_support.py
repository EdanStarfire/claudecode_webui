"""Helpers for unread monitor tests (issue #2103 stage 3c-ii): scripted conversations over the fake adapter."""

from __future__ import annotations

from backend.relay.adapter import ConversationInfo, RelayMessage
from backend.relay.inbound import constants as const
from backend.relay.inbound import schedule as sched
from backend.relay.inbound import unread
from backend.tests.relay_inbound_support import BASE_TIME, OTHER_USER, OWNER, SESSION, audit_events
from backend.tests.relay_monitor_support import CHANNEL, started

__all__ = ["BASE_TIME", "CHANNEL", "OTHER_USER", "OWNER", "SESSION", "UNREAD", "audit_events", "started",
           "at", "dm", "mpim", "chan", "msg", "add_unread", "create", "tick_at", "mon", "confirm",
           "rows", "entries_of", "interval"]

UNREAD = const.KIND_MONITOR_UNREAD
HOUR = 3600.0


def at(seconds: float) -> str:
    return f"{BASE_TIME + seconds:.6f}"


def msg(offset, text="hello", user=OTHER_USER, **kw) -> RelayMessage:
    return RelayMessage(ts=at(offset), text=text, user=user, **kw)


def dm(cid, *, unread_count=None, last_read=0, latest=None, **kw) -> ConversationInfo:
    return ConversationInfo(
        id=cid, is_im=True, unread_count=unread_count, last_read=at(last_read) if last_read is not None else None,
        latest_ts=at(latest) if latest is not None else None, **kw)


def mpim(cid, **kw) -> ConversationInfo:
    return ConversationInfo(id=cid, is_mpim=True, last_read=at(kw.pop("last_read", 0)),
                            latest_ts=at(kw.pop("latest")) if "latest" in kw else None, **kw)


def chan(cid, *, last_read=0, latest=None, is_member=True, **kw) -> ConversationInfo:
    return ConversationInfo(
        id=cid, is_channel=True, is_member=is_member, last_read=at(last_read) if last_read is not None else None,
        latest_ts=at(latest) if latest is not None else None, **kw)


def add_unread(env, info: ConversationInfo, *messages: RelayMessage) -> None:
    env.adapter.conversations[info.id] = info
    env.adapter.histories[info.id] = list(messages)


def interval(env, minutes=60):
    return sched.parse_schedule({"every_minutes": minutes}, owner_tz=None, now=env.clock.now(),
                                min_minutes=const.UNREAD_MIN_INTERVAL_MINUTES)


async def create(env, *, session=SESSION, minutes=60, **opts):
    options = unread.make_options(
        opts.get("dms", True), opts.get("channels", []), opts.get("exclude", []),
        opts.get("mentions_only", False), opts.get("repeat", False))
    rec, _ = await env.service.create_monitor(
        session, UNREAD, None, thread_ts=None, filters={"keywords": [], "users": []}, expires_at=None,
        schedule=interval(env, minutes), options=options)
    return rec


def mon(env, rec):
    return env.service.store.get_monitor(rec["id"])


async def tick_at(env, rec, *, plus: float = 1.0) -> int:
    now = env.clock.now()
    target = float(mon(env, rec)["next_run"]) + plus
    if target > now:
        env.clock.advance(target - now)
    return await env.service.digest.tick()


async def confirm(env, status="delivered"):
    for call in env.port.calls:
        key = (call["session_id"], call["comm_id"])
        if env.port.statuses.get(key) == "pending":
            env.port.statuses[key] = status
    await env.service.watcher.poll_once()


def rows(env, **match):
    return [r for r in audit_events(env, "monitor_digest") if all(r.get(k) == v for k, v in match.items())]


def entries_of(call) -> list[dict]:
    from backend.tests.relay_monitor_support import envelope_entries
    return envelope_entries(call)
