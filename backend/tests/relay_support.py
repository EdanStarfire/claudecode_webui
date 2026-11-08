"""Fakes for relay MCP/gate tests (issue #2103). No network, no vault."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.mcp.relay_mcp_tools import RelayMCPTools
from backend.relay.adapter import (
    AuthInfo,
    ConversationInfo,
    PostResult,
    RelayError,
    RelayMessage,
    ThreadPage,
    UploadResult,
    UserInfo,
)
from backend.relay.audit import RelayAuditLog
from backend.relay.gate import RelayPolicyGate
from backend.relay.grants import GrantStore
from backend.relay.permission_bridge import RelayPermissionBridge
from backend.relay.policy import EffectivePolicy, normalize_policies

SELF_DM = "D0SELF0001"
OWNER = "U0OWNER001"
OTHER_DM = "D0OTHER0001"
OTHER_USER = "U0OTHER001"
CH_AUTO = "C0AUTO0001"
CH_ASK = "C0ASK00001"
CH_RO = "C0READONLY1"
CH_BLOCKED = "C0BLOCKED01"
CH_UNLISTED = "C0UNLIST001"


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.search_results: list[RelayMessage] = []
        self.thread_messages: list[RelayMessage] = []
        self.fail_with: str | None = None
        self.users = {OWNER: "Owner Person", OTHER_USER: "Alice Example", "U0SELF0001": "Self"}
        self.fail_lookups = False
        self.fail_open_dm = False
        self.channels_error: str | None = None
        self.extra_channels: list[ConversationInfo] = []
        self.dms: list[ConversationInfo] = [
            ConversationInfo(id=SELF_DM, is_im=True, user=OWNER),
            ConversationInfo(id=OTHER_DM, is_im=True, user=OTHER_USER),
        ]
        self.user_list: list[UserInfo] = [
            UserInfo(id=OWNER, name="Owner Person", display_name="Owner Person", handle="owner"),
            UserInfo(id=OTHER_USER, name="Alice Example", display_name="Alice Example",
                     real_name="Alice Example", handle="alice"),
        ]
        self.names = {CH_AUTO: "auto-ch", CH_ASK: "ask-ch", CH_RO: "ro-ch",
                      CH_BLOCKED: "blocked-ch", CH_UNLISTED: "unlisted-ch"}

    def _maybe_fail(self, name: str) -> None:
        if self.fail_with:
            raise RelayError(self.fail_with, name)

    async def post(self, channel, text, blocks=None, thread_ts=None, metadata=None):
        self.calls.append(("post", channel, text, blocks, thread_ts, metadata))
        self._maybe_fail("chat.postMessage")
        return PostResult(channel=channel, ts="1700000000.000100")

    async def read_thread(self, channel, thread_ts, limit=100, cursor=None):
        self.calls.append(("read_thread", channel, thread_ts, limit, cursor))
        return ThreadPage(list(self.thread_messages), None)

    async def read_history(self, channel, limit=100, oldest=None, cursor=None):
        self.calls.append(("read_history", channel, limit))
        return ThreadPage([RelayMessage(ts="1700000009.000001", text="latest")], None)

    async def search(self, query, in_channels=None, limit=20):
        self.calls.append(("search", query, list(in_channels or []), limit))
        return list(self.search_results)

    async def upload(self, channel, path, thread_ts=None, comment=None, title=None):
        self.calls.append(("upload", channel, Path(path), thread_ts, comment, title))
        return UploadResult(file_id="F0001", title=title)

    async def react(self, channel, ts, name):
        self.calls.append(("react", channel, ts, name))

    async def mark_read(self, channel, ts):
        self.calls.append(("mark_read", channel, ts))

    async def conversation_info(self, channel):
        self.calls.append(("conversation_info", channel))
        if self.fail_lookups:
            raise RelayError("internal_error", "conversations.info")
        if channel == SELF_DM:
            return ConversationInfo(id=channel, is_im=True, user=OWNER)
        if channel == OTHER_DM:
            return ConversationInfo(id=channel, is_im=True, user=OTHER_USER)
        return ConversationInfo(id=channel, name=self.names.get(channel))

    async def user_info(self, user_id):
        self.calls.append(("user_info", user_id))
        if self.fail_lookups:
            raise RelayError("internal_error", "users.info")
        return UserInfo(id=user_id, name=self.users.get(user_id))

    async def list_conversations(self, types="public_channel,private_channel,mpim,im", limit=200):
        self.calls.append(("list_conversations", types))
        if types in ("im,mpim",):
            return list(self.dms)
        return [ConversationInfo(id=i, name=n) for i, n in self.names.items()]

    async def list_channels(self, types="public_channel,private_channel", limit=1000):
        self.calls.append(("list_channels", types))
        if self.channels_error:
            raise RelayError(self.channels_error, "conversations.list")
        return [ConversationInfo(id=i, name=n, is_channel=True) for i, n in self.names.items()] + list(
            self.extra_channels
        )

    async def list_users(self, limit=5000):
        self.calls.append(("list_users",))
        return list(self.user_list)

    async def auth_test(self):
        self.calls.append(("auth_test",))
        return AuthInfo(user_id=OWNER, team_id="T0TEAM001", team="Team", user="owner")

    async def open_dm(self, user_id):
        self.calls.append(("open_dm", user_id))
        if self.fail_open_dm:
            raise RelayError("internal_error", "conversations.open")
        return OTHER_DM if user_id == OTHER_USER else SELF_DM

    def network_calls(self) -> list[tuple]:
        """Calls that touch message content (anything but name/DM resolution)."""
        resolution = {
            "conversation_info", "list_conversations", "open_dm", "user_info", "list_channels",
            "list_users", "auth_test",
        }
        return [c for c in self.calls if c[0] not in resolution]


class FakeSessions:
    def __init__(self, info, session_dir: Path | None = None) -> None:
        self.info = info
        self.session_dir = session_dir

    async def get_session_info(self, session_id):
        return self.info if session_id == self.info.session_id else None

    async def get_session_directory(self, session_id):
        return self.session_dir

    async def update_relay_policy(self, session_id, relay_id, target, policy, *, sole_relay_id=None):
        policies = normalize_policies(self.info.config.get("relay_channel_policies"), sole_relay_id)
        entry = dict(policies.get(relay_id) or {})
        if policy is None:
            entry.pop(target, None)
        else:
            entry[target] = policy
        policies[relay_id] = entry
        self.info.config["relay_channel_policies"] = policies
        return True


class FakeService:
    """Live policy view over the fake session config (same semantics as RelayService)."""

    def __init__(self, sessions: FakeSessions) -> None:
        self.sessions = sessions

    async def effective_relay_policies(self, info, relay_id):
        return {
            c: EffectivePolicy(p, "session")
            for c, p in (info.config.get("relay_channel_policies") or {}).get(relay_id, {}).items()
        }

    async def effective_attachment(self, info):
        cfg = info.config
        return {
            "relay_id": cfg.get("relay_id"),
            "relay_footer_enabled": cfg.get("relay_footer_enabled"),
            "relay_footer_template": cfg.get("relay_footer_template"),
        }

    async def ensure_handle(self, session_id):
        return self.sessions.info.config.get("relay_handle")

    async def set_policy(self, session_id, relay_id, channel_id, policy):
        await self.sessions.update_relay_policy(session_id, relay_id, channel_id, policy)


class FakeRelayManager:
    def __init__(self, adapter: FakeAdapter, relay_id: str = "relay1") -> None:
        self.relays = {relay_id: SimpleNamespace(
            owner_user_id=OWNER, adapter_type="slack", self_dm_channel_id=None,
            workspace_id="T0TEAM001", workspace_name="Team",
        )}
        self.recorded_self_dm: list[tuple] = []
        self._adapter = adapter

    def has_token(self, relay_id, kind="user"):
        return True

    def adapter_for(self, relay_id):
        return self._adapter

    def record_self_dm(self, relay_id, owner_user_id, dm_channel_id):
        self.recorded_self_dm.append((relay_id, owner_user_id, dm_channel_id))
        self.relays[relay_id].self_dm_channel_id = dm_channel_id


def make_env(tmp_path: Path, policies: dict | None = None, **config):
    adapter = FakeAdapter()
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    cfg = {
        "relay_id": "relay1",
        "relay_handle": "my-agent",
        "relay_channel_policies": {"relay1": dict(
            policies
            if policies is not None
            else {SELF_DM: "auto", CH_AUTO: "auto", CH_ASK: "ask", CH_RO: "read_only",
                  CH_BLOCKED: "blocked"}
        )},
        **config,
    }
    info = SimpleNamespace(
        session_id="s1", name="My Session", working_directory=str(workdir), config=cfg
    )
    sessions = FakeSessions(info, tmp_path / "sessiondata")
    service = FakeService(sessions)
    manager = FakeRelayManager(adapter)
    gate = RelayPolicyGate(service, manager, sessions, GrantStore())
    audit = RelayAuditLog(tmp_path)
    tools = RelayMCPTools(gate, audit)
    bridge = RelayPermissionBridge(gate, service, audit)
    return SimpleNamespace(
        adapter=adapter, info=info, sessions=sessions, service=service, gate=gate,
        audit=audit, tools=tools, bridge=bridge, workdir=workdir,
    )


def result_text(result: dict) -> str:
    return "\n".join(b["text"] for b in result["content"])
