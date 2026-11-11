"""Fakes for relay inbound tests (issue #2103 stage 3a). No network, no vault, no Slack."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.relay.adapter import (
    ConversationInfo,
    PostResult,
    RelayError,
    RelayMessage,
    SearchHit,
    SearchPage,
    ThreadPage,
)
from backend.relay.audit import RelayAuditLog
from backend.relay.handles import RelayHandleIndex
from backend.relay.inbound.service import DeliveryOutcome, InboundService
from backend.relay.inbound.transport import RawEnvelope
from backend.session_manager import SessionState

RELAY_ID = "relay1"
TEAM = "T0TEAM001"
OWNER = "U0OWNER001"
SELF_DM = "D0SELF0001"
OTHER_DM = "D0OTHER0001"
OTHER_USER = "U0OTHER001"
CHANNEL = "C0CHAN0001"
SESSION = "s1"
BASE_TIME = 1_800_000_000.0


class FakeClock:
    def __init__(self, start: float = BASE_TIME) -> None:
        self.t = start
        self.mono_t = 1000.0

    def now(self) -> float:
        return self.t

    def mono(self) -> float:
        return self.mono_t

    def advance(self, seconds: float) -> None:
        self.t += seconds
        self.mono_t += seconds

    def ts(self, offset: float = 0.0, seq: int = 0) -> str:
        """A Slack-style ts at now+offset (``seq`` keeps same-second messages distinct)."""
        micros = int(round((self.t + offset) * 1_000_000)) + seq
        return f"{micros // 1_000_000}.{micros % 1_000_000:06d}"


class FakeSocketTransport:
    """Stands in for slack_sdk: ``emit`` delivers an envelope and acks after the handler returns."""

    def __init__(self, *, connect_error: Exception | None = None) -> None:
        self.connect_error = connect_error
        self.on_envelope = None
        self.on_state = None
        self.connected = False
        self.connect_count = 0
        self.disconnect_count = 0
        self.acked: list[str] = []
        self.unacked: list[str] = []

    def set_handlers(self, on_envelope, on_state) -> None:
        self.on_envelope, self.on_state = on_envelope, on_state

    async def connect(self) -> None:
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        self.connected = False

    async def emit(self, env: RawEnvelope) -> bool:
        try:
            await self.on_envelope(env)
        except Exception:
            self.unacked.append(env.envelope_id)
            raise
        if env.envelope_id:
            self.acked.append(env.envelope_id)
        return True

    async def hello(self) -> None:
        self.on_state("connected")
        await self.emit(RawEnvelope("", "hello", {}))

    def drop(self) -> None:
        self.on_state("dropped")

    def auth_failed(self) -> None:
        self.on_state("auth_failed")


def message_payload(
    text: str, ts: str, *, channel: str = SELF_DM, user: str | None = OWNER,
    thread_ts: str | None = None, channel_type: str = "im", team: str = TEAM, subtype=None,
    app_id=None, bot_id=None, metadata=None, event_type: str = "message", files=None, **extra,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": event_type, "channel": channel, "channel_type": channel_type, "ts": ts,
        "text": text,
    }
    for key, value in (("user", user), ("thread_ts", thread_ts), ("subtype", subtype),
                       ("app_id", app_id), ("bot_id", bot_id), ("metadata", metadata),
                       ("files", files)):
        if value is not None:
            event[key] = value
    event.update(extra)
    return {"type": "event_callback", "team_id": team, "event": event}


def envelope(payload: dict, eid: str = "E1", retry: int = 0) -> RawEnvelope:
    return RawEnvelope(eid, "events_api", payload, retry)


class FakeAdapter:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.history: list[RelayMessage] = []
        self.history_calls: list[dict[str, Any]] = []
        self.threads: dict[str, list[RelayMessage]] = {}
        self.fail_post = False
        self.reactions: list[tuple[str, str, str, str]] = []  # (op, channel, ts, name)
        self.react_error: str | None = None
        self.react_hang = False
        self.unreact_error: str | None = None
        self.history_next: dict[str | None, str | None] = {}
        self.histories: dict[str, list[RelayMessage]] = {}
        self.thread_calls: list[dict[str, Any]] = []
        self.channel_names: dict[str, str] = {}
        self.user_names: dict[str, str] = {}
        self.member: bool | None = True
        # --- scope monitors (3b-ii)
        self.conversations: dict[str, ConversationInfo] = {}  # id -> info (list / info calls)
        self.search_hits: list[SearchHit] = []  # newest first, as search.messages sort=timestamp desc
        self.search_visible: int | None = None  # index lag: only the first N hits are visible
        self.search_calls: list[dict[str, Any]] = []
        self.thread_parents: dict[str, RelayMessage] = {}  # thread_ts -> parent (always returned first)
        self.call_log: list[tuple[str, str]] = []  # (method, channel)
        self.fail_methods: dict[str, str] = {}  # method -> error code raised by that method
        self.list_calls: list[str] = []
        self.fail_channels: dict[str, str] = {}  # channel -> error code for info / history on it

    async def post(self, channel, text, blocks=None, thread_ts=None, metadata=None):
        if self.fail_post:
            from backend.relay.adapter import RelayError

            raise RelayError("internal_error", "chat.postMessage")
        self.posts.append({
            "channel": channel, "text": text, "blocks": blocks, "thread_ts": thread_ts,
            "metadata": metadata,
        })
        return PostResult(channel, f"{BASE_TIME + len(self.posts):.6f}")

    async def react(self, channel, ts, name, *, retry=True):
        from backend.relay.adapter import RelayError

        self.reactions.append(("add", channel, ts, name))
        if self.react_hang:
            await asyncio.sleep(3600)
        if self.react_error:
            raise RelayError(self.react_error, "reactions.add")

    async def unreact(self, channel, ts, name, *, retry=True):
        from backend.relay.adapter import RelayError

        self.reactions.append(("remove", channel, ts, name))
        if self.unreact_error:
            raise RelayError(self.unreact_error, "reactions.remove")

    def _maybe_fail(self, method: str) -> None:
        code = self.fail_methods.get(method)
        if code:
            raise RelayError(code, method)

    @staticmethod
    def _in_range(msg, oldest, latest, inclusive) -> bool:
        ts = float(msg.ts)
        if oldest is not None and not (ts >= float(oldest) if inclusive else ts > float(oldest)):
            return False
        return not (latest is not None and not (ts <= float(latest) if inclusive else ts < float(latest)))

    async def read_history(self, channel, limit=100, oldest=None, cursor=None, latest=None, inclusive=False):
        self.history_calls.append({"channel": channel, "oldest": oldest, "cursor": cursor,
                                   "latest": latest, "inclusive": inclusive})
        self.call_log.append(("history", channel))
        self._maybe_fail("read_history")
        source = self.histories.get(channel, self.history)
        msgs = [m for m in source if self._in_range(m, oldest, latest, inclusive)]
        return ThreadPage(msgs if cursor is None else [], self.history_next.get(cursor))

    async def read_thread(self, channel, thread_ts, limit=100, cursor=None, oldest=None, latest=None,
                          inclusive=False):
        self.thread_calls.append({"channel": channel, "thread_ts": thread_ts, "oldest": oldest,
                                  "latest": latest, "inclusive": inclusive})
        self.call_log.append(("thread", channel))
        self._maybe_fail("read_thread")
        msgs = [m for m in self.threads.get(thread_ts, []) if self._in_range(m, oldest, latest, inclusive)]
        parent = self.thread_parents.get(thread_ts)
        if parent is not None:
            msgs = [parent, *msgs]
        return ThreadPage(msgs, None)

    async def search_page(self, query, page=1, count=100, sort="timestamp", sort_dir="desc"):
        self.search_calls.append({"query": query, "page": page, "sort": sort, "sort_dir": sort_dir})
        self._maybe_fail("search_page")
        hits = self.search_hits if self.search_visible is None else self.search_hits[: self.search_visible]
        pages = max(1, -(-len(hits) // count))
        return SearchPage(hits[(page - 1) * count: page * count], page, pages)

    async def list_conversations(self, types="public_channel,private_channel,mpim,im", limit=200):
        self.list_calls.append(types)
        self._maybe_fail("list_conversations")
        wanted = set(types.split(","))
        out = []
        for c in self.conversations.values():
            kind = "im" if c.is_im else "mpim" if c.is_mpim else "private_channel" if c.is_private else "public_channel"
            if kind in wanted:
                out.append(c)
        return out

    async def conversation_info(self, channel):
        self.call_log.append(("info", channel))
        self._maybe_fail("conversation_info")
        if channel in self.fail_channels:
            raise RelayError(self.fail_channels[channel], "conversations.info")
        if channel in self.conversations:
            return self.conversations[channel]
        return ConversationInfo(id=channel, name=self.channel_names.get(channel, "general"),
                                is_channel=True, is_member=self.member)

    async def user_info(self, user_id):
        from backend.relay.adapter import UserInfo

        return UserInfo(id=user_id, name=self.user_names.get(user_id, f"user-{user_id}"))

    async def open_dm(self, user_id):
        return SELF_DM


class FakeRelayManager:
    def __init__(self, adapter: FakeAdapter) -> None:
        self.adapter = adapter
        self.relays = {RELAY_ID: SimpleNamespace(
            relay_id=RELAY_ID, name="Slack", owner_user_id=OWNER, self_dm_channel_id=SELF_DM,
            workspace_id=TEAM, inbound_enabled=True, app_token_secret="app-secret",
            user_token_secret="user-secret",
        )}
        self.tokens = {"user": True, "app": True}

    def has_token(self, relay_id, kind="user"):
        return self.tokens.get(kind, False)

    def adapter_for(self, relay_id):
        return self.adapter

    def record_self_dm(self, relay_id, owner, dm):
        self.relays[relay_id].self_dm_channel_id = dm


class FakeSessions:
    def __init__(self) -> None:
        self.infos: dict[str, Any] = {}

    def add(self, session_id=SESSION, *, state=SessionState.ACTIVE, ephemeral=False,
            relay_id=RELAY_ID):
        self.infos[session_id] = SimpleNamespace(
            session_id=session_id, state=state, is_ephemeral=ephemeral, name=f"Session {session_id}",
            config={"relay_id": relay_id, "relay_handle": f"handle-{session_id}"},
        )

    async def get_session_info(self, session_id):
        return self.infos.get(session_id)

    def has_session(self, session_id):
        return session_id in self.infos


class FakeRelayService:
    def __init__(self, sessions: FakeSessions, handles: RelayHandleIndex) -> None:
        self.handles = handles
        self._sessions = sessions
        # channel -> policy value; tests edit it live (blocked | read_only | ask | auto)
        self.policies: dict[str, str] = {}
        self.refreshed: list[str] = []  # sessions whose relay_summary was recomputed (3d-ii)

    async def refresh_summary(self, session_id):
        self.refreshed.append(session_id)

    async def effective_attachment(self, info):
        return {"relay_id": info.config.get("relay_id")}

    async def effective_relay_policies(self, info, relay_id):
        return {c: SimpleNamespace(policy=p, source="session") for c, p in self.policies.items()}


class FakePort:
    """Monitor delivery port: records envelopes; tests move item statuses by hand."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.statuses: dict[tuple[str, str], str] = {}
        self.fail_enqueue = False
        self.pending_monitor: bool | int = False
        self.on_enqueue = None  # async hook awaited inside enqueue (tests interleave work there)

    async def enqueue(self, session_id, **kwargs):
        if self.fail_enqueue:
            return DeliveryOutcome("failed", "boom")
        self.calls.append({"session_id": session_id, **kwargs})
        if self.on_enqueue is not None:
            await self.on_enqueue()
        key = (session_id, kwargs["comm_id"])
        if key in self.statuses:
            return DeliveryOutcome("already_delivered")
        self.statuses[key] = "pending"
        return DeliveryOutcome("delivered")

    def item_status(self, session_id, comm_id):
        return self.statuses.get((session_id, comm_id))

    def pending_monitor_count(self, session_id, relay_id):
        monitor_ids = {c["comm_id"] for c in self.calls}
        n = sum(
            1 for (sid, cid), st in self.statuses.items()
            if st == "pending" and sid == session_id and cid in monitor_ids
        )
        return max(n, int(self.pending_monitor))

    async def cancel(self, session_id, comm_id):
        if self.statuses.get((session_id, comm_id)) != "pending":
            return False
        self.statuses[(session_id, comm_id)] = "cancelled"
        return True

    def set_status(self, comm_id, status, session_id=SESSION):
        self.statuses[(session_id, comm_id)] = status


class ManualSleep:
    """Window / postponement sleeps that only finish when the test says so (advancing the clock)."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.waiting: list[tuple[float, asyncio.Future]] = []
        self.requested: list[float] = []

    async def __call__(self, seconds: float) -> None:
        fut = asyncio.get_running_loop().create_future()
        self.waiting.append((seconds, fut))
        self.requested.append(seconds)
        await fut

    async def fire(self) -> bool:
        """Finish the oldest pending sleep; True when there was one."""
        for _ in range(100):
            if self.waiting:
                break
            await asyncio.sleep(0.002)
        if not self.waiting:
            return False
        seconds, fut = self.waiting.pop(0)
        self.clock.advance(seconds)
        fut.set_result(None)
        await asyncio.sleep(0.01)
        return True

    async def drain(self, rounds: int = 30) -> None:
        for _ in range(rounds):
            if not await self.fire():
                return
            await asyncio.sleep(0.02)


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = DeliveryOutcome("delivered")
        self.seen_comm_ids: set[str] = set()
        self.raise_error = False

    async def __call__(self, session_id, **kwargs):
        if self.raise_error:
            raise RuntimeError("boom")
        self.calls.append({"session_id": session_id, **kwargs})
        if kwargs["comm_id"] in self.seen_comm_ids:
            return DeliveryOutcome("already_delivered")
        self.seen_comm_ids.add(kwargs["comm_id"])
        return self.outcome


def make_inbound(tmp_path: Path, *, handles: dict[str, str] | None = None, factory=None,
                 clock: FakeClock | None = None):
    clock = clock or FakeClock()
    adapter = FakeAdapter()
    manager = FakeRelayManager(adapter)
    sessions = FakeSessions()
    sessions.add()
    index = RelayHandleIndex(sessions.has_session)
    for handle, sid in (handles if handles is not None else {"my-agent": SESSION}).items():
        index.rebind(sid, handle)
    sink = RecordingSink()
    port = FakePort()
    window = ManualSleep(clock)
    audit = RelayAuditLog(tmp_path)
    transports: list[FakeSocketTransport] = []
    delays: list[float] = []

    def default_factory(relay_id):
        t = FakeSocketTransport()
        transports.append(t)
        return t

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        clock.advance(seconds)
        await asyncio.sleep(0)

    service_relay = FakeRelayService(sessions, index)
    rerun = ManualSleep(clock)  # the delayed mentions re-run only happens when a test fires it
    search_sleeps: list[float] = []

    async def search_sleep(seconds: float) -> None:
        search_sleeps.append(seconds)
        clock.advance(seconds)

    service = InboundService(
        RELAY_ID, relay_manager=manager, relay_service=service_relay,
        session_manager=sessions, audit=audit, sink=sink, relays_root=tmp_path / "relays",
        transport_factory=factory or default_factory, now=clock.now, mono=clock.mono,
        sleep=fake_sleep, rng=lambda: 1.0, port=port, window_sleep=window,
        watch_sleep=ManualSleep(clock), search_sleep=search_sleep, rerun_sleep=rerun,
    )
    return SimpleNamespace(rerun=rerun, search_sleeps=search_sleeps,
        service=service, adapter=adapter, manager=manager, sessions=sessions, index=index,
        sink=sink, audit=audit, clock=clock, transports=transports, delays=delays, port=port,
        window=window, relay_service=service_relay,
        root=tmp_path / "relays" / RELAY_ID,
    )


async def settle(env, *, until=None, rounds: int = 200) -> None:
    """Let the worker/supervisor tasks run until idle (or ``until()``). Real time passes between
    rounds because state/audit writes go through ``asyncio.to_thread``."""
    deadline = asyncio.get_running_loop().time() + 5.0
    quiet = 0
    for _ in range(rounds * 50):
        await asyncio.sleep(0.002)
        if until is not None:
            if until():
                return
            continue
        if not env.service.state.inflight and env.service._queue.empty():
            quiet += 1
            if quiet >= 5:
                return
        else:
            quiet = 0
        if asyncio.get_running_loop().time() > deadline:
            return


async def started(env):
    """Start the service and wait until the fake transport is connected."""
    await env.service.start()
    await settle(env, until=lambda: bool(env.transports) and env.transports[-1].connected)
    return env.transports[-1]


def audit_events(env, event: str | None = None) -> list[dict]:
    path = env.root / "audit.jsonl"
    if not path.exists():
        return []
    import json

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return [r for r in rows if event is None or r["event"] == event]
