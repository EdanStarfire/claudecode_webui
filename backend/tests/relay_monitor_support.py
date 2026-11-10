"""Helpers for monitor tests (issue #2103 stage 3b-i): fake transport + fake clock + manual windows."""

from __future__ import annotations

import asyncio
import json

from backend.relay.adapter import RelayMessage
from backend.relay.inbound import constants as const
from backend.tests.relay_inbound_support import (
    CHANNEL,
    OTHER_USER,
    SESSION,
    envelope,
    message_payload,
    started,
)

__all__ = ["CHANNEL", "OTHER_USER", "SESSION", "started", "monitor", "emit", "fire_window",
           "envelope_entries", "history_msg", "wait_for"]


async def monitor(env, *, kind=const.KIND_MONITOR_CHANNEL, channel=CHANNEL, thread_ts=None,
                  keywords=("deploy",), users=(), session=SESSION, expires_at=None):
    rec, _ = await env.service.create_monitor(
        session, kind, channel, thread_ts=thread_ts,
        filters={"keywords": list(keywords), "users": list(users)}, expires_at=expires_at,
    )
    return rec


async def emit(env, transport, text, *, seq, channel=CHANNEL, user=OTHER_USER, thread_ts=None, **kw):
    """A live channel message at now+seq µs; returns its ts."""
    ts = env.clock.ts(seq=seq)
    kw.setdefault("channel_type", "channel")
    await transport.emit(envelope(
        message_payload(text, ts, channel=channel, user=user, thread_ts=thread_ts, **kw), f"E{seq}"))
    return ts


async def fire_window(env) -> None:
    """Let the 60 s window (and any postponement) elapse and the flush finish."""
    await env.window.drain()
    await asyncio.sleep(0.05)


def envelope_entries(call) -> list[dict]:
    """The fenced JSON entries of a delivered envelope."""
    text = call["dispatch_content"]
    start = text.index("]\n", text.index("[UNTRUSTED EXTERNAL CONTENT")) + 2
    end = text.index("\n[END UNTRUSTED EXTERNAL CONTENT")
    return json.loads(text[start:end])


def history_msg(ts, text="deploy it", user=OTHER_USER, thread_ts=None, **kw) -> RelayMessage:
    return RelayMessage(ts=ts, text=text, user=user, thread_ts=thread_ts, **kw)


async def wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")
