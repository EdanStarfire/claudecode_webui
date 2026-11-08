"""Harness for issue #2353 tests: real PermissionService + ToolCallTracker + RelayPromptBroker
over the fake relay env (no CLI, no network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.models.messages import ToolCall, ToolState
from backend.permission_service import PermissionService
from backend.relay.call_registry import RelayCallRegistry
from backend.relay.prompt_broker import RelayPromptBroker
from backend.session_manager import SessionState
from backend.tests.relay_support import make_env
from backend.tool_call_tracker import ToolCallTracker
from shared.event_queue import EventQueue

SID = "s1"


class FakeStorage:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def append_message(self, record):
        self.records.append(record)


class FakeSessionManager:
    def __init__(self, info) -> None:
        self.info = info
        self.pauses = 0

    async def get_session_info(self, session_id):
        return self.info if session_id == self.info.session_id else None

    async def pause_session(self, session_id):
        self.info.state = SessionState.PAUSED
        self.pauses += 1
        return True

    async def resolve_permission_closed(self, session_id):
        if self.info.state == SessionState.PAUSED:
            self.info.state = SessionState.ACTIVE
        return True


class StrictInfo:
    """Session info that fails if anything reads the permission mode (mode must never matter)."""

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        if "permission_mode" in name:
            raise AssertionError(f"permission mode was consulted: {name}")
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


class FakeCoordinator:
    """Just enough SessionCoordinator for PermissionService + the broker; tool-call state is
    the real ToolCallTracker."""

    def __init__(self, env, storage: FakeStorage | None = None) -> None:
        self._active_tool_calls: dict[str, dict[str, ToolCall]] = {}
        self._tool_call_events: dict[str, asyncio.Event] = {}
        self._tool_call_broadcast_callbacks: dict = {}
        self._storage_managers = {SID: storage} if storage else {}
        self._watchdog = None
        self.get_or_create_storage_manager = lambda sid: None
        env.info.is_ephemeral = False
        env.info.state = SessionState.ACTIVE
        self.session_manager = FakeSessionManager(StrictInfo(env.info))
        self.tenant_context = SimpleNamespace(backend_id="b1")
        self._active_sdks = {
            SID: SimpleNamespace(
                permission_callback=lambda *a: None, deny_unattended_permission_prompts=False
            )
        }
        self.tracker = ToolCallTracker(coordinator=self)
        self.permission_service = None
        self.relay = SimpleNamespace(gate=env.gate, broker=None)

    # tool-call API used by PermissionService / the broker
    def __getattr__(self, name):
        if name in {
            "create_tool_call", "update_tool_call_permission_request",
            "update_tool_call_permission_response", "update_tool_call_result",
            "get_tool_call_by_id", "get_tool_call_by_request_id", "get_tool_call_event",
        }:
            return getattr(self.tracker, name)
        raise AttributeError(name)

    def is_uploaded_file(self, session_id, path):
        return False

    def is_assistant_message_emitted(self, session_id, turn_id):
        return True


class Harness:
    def __init__(self, tmp_path, policies=None, **config) -> None:
        self.env = make_env(tmp_path, policies, **config)
        self.storage = FakeStorage()
        self.coord = FakeCoordinator(self.env, self.storage)
        self.queue = EventQueue()
        self.service = PermissionService(self.coord, {SID: self.queue})
        self.coord.permission_service = self.service
        self.coord.relay_bridge = self.env.bridge
        self.registry = RelayCallRegistry()
        self.broker = RelayPromptBroker(self.registry, self.env.bridge)
        self.broker.attach(self.coord)
        self.coord.relay.broker = self.broker
        self.env.tools.broker = self.broker
        self.adapter = self.env.adapter
        self.tools = self.env.tools
        self.gate = self.env.gate

    def add_card(self, tool_use_id: str, args: dict, tool="post", status=ToolState.PENDING):
        card = self.coord.create_tool_call(
            SID, tool_use_id, f"mcp__relay_slack__{tool}", args
        )
        card.status = status
        return card

    def hook(self, tool_use_id: str, args: dict, tool="post", agent_id=None):
        self.registry.record(SID, f"mcp__relay_slack__{tool}", args, tool_use_id, agent_id)

    async def wait_pending(self, count=1, timeout=3.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while len(self.service.pending_permissions) < count:
            if loop.time() > end:
                raise AssertionError(f"no prompt raised ({len(self.service.pending_permissions)})")
            await asyncio.sleep(0.01)
        return list(self.service.pending_permissions)

    def answer(self, request_id: str, response: dict) -> bool:
        return self.service.resolve(request_id, response)

    def audit_rows(self):
        import json
        path = self.env.audit._path("relay1")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]


    def tool_call_events(self, tool_use_id: str | None = None) -> list[dict]:
        """tool_call payloads emitted on the session queue (optionally for one card)."""
        events, _, _ = self.queue.events_since(0)
        out = []
        for ev in events:
            payload = ev.get("payload", ev)
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and (ev.get("event_type") or ev.get("type")) in (None, "tool_call") \
                    and (tool_use_id is None or data.get("tool_use_id") == tool_use_id):
                out.append(data)
        return out


def settle():
    return asyncio.sleep(0.05)
