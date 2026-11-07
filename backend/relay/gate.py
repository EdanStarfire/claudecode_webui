"""RelayPolicyGate: channel resolution + policy decision for relay tool calls (issue #2103).

One decision path serves both gates: the permission-callback pre-hook (``evaluate``) and the
authoritative tool-handler check (``authorize``). The handler's check is the one that
cannot be bypassed: ``ask`` sends need a one-shot grant, unlisted channels need a chosen
policy, and text containing ``!handle`` trigger syntax is refused before any network call.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from .adapter import RelayAdapter, RelayError
from .grants import Grant, GrantStore
from .names import NameResolver
from .policy import (
    ALLOW,
    CHANNEL_ID_RE,
    DENY,
    PROMPT,
    decide,
)
from .safety import contains_trigger_syntax, safe_error_code

if TYPE_CHECKING:
    from backend.relay_manager import RelayManager
    from backend.session_manager import SessionInfo, SessionManager

    from .service import RelayService

TOOL_PREFIX = "mcp__relay__"
RELAY_TOOLS = ("post", "reply", "read_thread", "search", "upload_file", "react", "mark_read")
# tool -> policy action (None: scope-filtered per channel inside the handler)
TOOL_ACTIONS: dict[str, str | None] = {
    "read_thread": "read",
    "mark_read": "mark_read",
    "post": "send",
    "reply": "send",
    "upload_file": "send",
    "react": "send",
    "search": None,
}
WRITE_TOOLS = frozenset({"post", "reply", "upload_file", "react", "mark_read"})
# U… (and Enterprise Grid W…) are user IDs, never conversation IDs: they resolve to a DM.
_USER_ID_RE = re.compile(r"^@?([UW][A-Z0-9]{6,})\Z")
_NAME_RE = re.compile(r"^#?([a-z0-9][a-z0-9._-]{0,79})\Z")
_NAME_CACHE_TTL = 300.0
PREVIEW_LEN = 40000  # the prompt shows everything that will be sent (bodies are capped at 35000)


def tool_short_name(tool_name: str) -> str | None:
    """``mcp__relay__post`` / ``post`` -> ``post``; None for non-relay tools."""
    short = tool_name[len(TOOL_PREFIX):] if tool_name.startswith(TOOL_PREFIX) else tool_name
    return short if short in TOOL_ACTIONS else None


class GateError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class RelayContext:
    session_id: str
    relay_id: str
    session_info: SessionInfo
    adapter: RelayAdapter
    handle: str | None
    owner_user_id: str | None
    footer_enabled: bool
    footer_template: str | None


@dataclass
class GateResult:
    kind: str  # allow | deny | prompt | choose_policy
    tool: str
    action: str | None = None
    code: str | None = None
    message: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None  # plain name for the owner's prompt/audit; never sent to the agent
    channel_label: str | None = None  # '#name' / 'DM with X' / 'your self-DM'
    policy: str | None = None
    prompt: dict[str, Any] | None = field(default=None)

    def error_result(self) -> dict[str, Any]:
        return error_result(
            self.code or "denied", self.message or "Not permitted.", self.channel_id,
            self.action or self.tool, self.policy,
        )


def error_result(
    code: str, message: str, channel: str | None, action: str, policy: str | None
) -> dict[str, Any]:
    """Structured policy refusal: human text block + machine-readable JSON block."""
    detail = {"error": "relay_policy", "code": code, "channel": channel, "action": action,
              "policy": policy}
    return {
        "content": [
            {"type": "text", "text": message},
            {"type": "text", "text": json.dumps(detail)},
        ],
        "is_error": True,
    }


class RelayPolicyGate:
    def __init__(
        self,
        relay_service: RelayService,
        relay_manager: RelayManager,
        session_manager: SessionManager,
        grants: GrantStore | None = None,
        *,
        clock=time.monotonic,
    ) -> None:
        self.service = relay_service
        self.relay_manager = relay_manager
        self.sessions = session_manager
        self.grants = grants or GrantStore()
        self._clock = clock
        self.names = NameResolver()
        self._by_name: dict[str, str] = {}  # name -> channel id
        self._names_loaded_at: float | None = None

    # --------------------------------------------------------------- context

    async def context(self, session_id: str) -> RelayContext | None:
        info = await self.sessions.get_session_info(session_id)
        if info is None:
            return None
        attachment = await self.service.effective_attachment(info)
        relay_id = attachment["relay_id"]
        if not relay_id or relay_id not in self.relay_manager.relays:
            return None
        if not self.relay_manager.has_token(relay_id, "user"):
            return None
        enabled = attachment["relay_footer_enabled"]
        return RelayContext(
            session_id=session_id,
            relay_id=relay_id,
            session_info=info,
            adapter=self.relay_manager.adapter_for(relay_id),
            handle=info.config.get("relay_handle") or await self.service.ensure_handle(session_id),
            owner_user_id=getattr(self.relay_manager.relays[relay_id], "owner_user_id", None),
            footer_enabled=True if enabled is None else bool(enabled),
            footer_template=attachment["relay_footer_template"],
        )

    # ------------------------------------------------------- channel handling

    async def _load_names(self, ctx: RelayContext, *, force: bool = False) -> None:
        now = self._clock()
        if (
            not force
            and self._names_loaded_at is not None
            and now - self._names_loaded_at < _NAME_CACHE_TTL
        ):
            return
        convs = await ctx.adapter.list_conversations("public_channel,private_channel", 1000)
        for c in convs:
            if c.name:
                self.names.seed_channel(ctx.relay_id, c.id, c.name)
                self._by_name[c.name] = c.id
        self._names_loaded_at = now

    async def resolve_channel(self, ctx: RelayContext, ref: Any) -> str:
        """Resolve an ID, ``#name`` or user ID (DM) to a canonical conversation ID."""
        if not isinstance(ref, str) or not ref.strip():
            raise GateError("channel_unresolved", "A 'channel' argument is required.")
        ref = ref.strip()
        user = _USER_ID_RE.match(ref)
        if CHANNEL_ID_RE.match(ref) and not user:
            return ref
        try:
            if user:
                return await ctx.adapter.open_dm(user.group(1))
            named = _NAME_RE.match(ref)
            if named:
                name = named.group(1)
                await self._load_names(ctx)
                if name not in self._by_name:
                    await self._load_names(ctx, force=True)
                if name in self._by_name:
                    return self._by_name[name]
        except RelayError as e:
            raise GateError("channel_unresolved", f"Could not resolve channel ({safe_error_code(e.code)}).") from None
        raise GateError(
            "channel_unresolved",
            "Could not resolve the channel. Use a channel ID (C…/G…/D…), #channel-name, or a "
            "user ID (U…) to message a person; '@name' is not supported, only user IDs.",
        )

    async def label(self, ctx: RelayContext, channel_id: str) -> str:
        """Owner-facing label (never sent to the agent); the ID when it cannot be resolved."""
        return await self.names.channel_label(
            ctx.adapter, ctx.relay_id, channel_id, ctx.owner_user_id
        )

    # --------------------------------------------------------------- decisions

    @staticmethod
    def outbound_texts(tool: str, args: dict[str, Any]) -> list[str]:
        if tool in ("post", "reply"):
            return [str(args.get("text") or "")]
        if tool == "upload_file":
            # the file name is posted too (upload title / filename)
            name = PurePath(str(args.get("path") or "")).name
            return [str(args.get("comment") or ""), name]
        return []

    @staticmethod
    def _preview(tool: str, args: dict[str, Any]) -> str:
        if tool in ("post", "reply"):
            return str(args.get("text") or "")[:PREVIEW_LEN]
        if tool == "upload_file":
            comment = str(args.get("comment") or "")
            return f"{args.get('path')}" + (f" — {comment[:PREVIEW_LEN]}" if comment else "")
        if tool == "react":
            return f":{str(args.get('name') or '').strip(':')}: on message {args.get('ts')}"
        if tool == "mark_read":
            return f"mark read up to {args.get('ts') or 'latest'}"
        return ""

    async def evaluate(
        self,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        ctx: RelayContext | None = None,
        *,
        with_prompt: bool = True,
    ) -> GateResult:
        tool = tool_short_name(tool_name)
        if tool is None:
            return GateResult(DENY, tool_name, code="unknown_tool", message="Unknown relay tool.")
        action = TOOL_ACTIONS[tool]
        ctx = ctx or await self.context(session_id)
        if ctx is None:
            return GateResult(
                DENY, tool, action, "relay_not_attached",
                "No relay with a user token is attached to this session.",
            )
        if action is None:  # search: filtered per channel in the handler
            return GateResult(ALLOW, tool)

        if any(contains_trigger_syntax(t) for t in self.outbound_texts(tool, args)):
            return GateResult(
                DENY, tool, action, "trigger_syntax_refused",
                "Outbound text must not contain !handle trigger syntax. Remove the '!' before the "
                "word and try again.",
                channel_id=args.get("channel") if isinstance(args.get("channel"), str) else None,
            )
        try:
            channel_id = await self.resolve_channel(ctx, args.get("channel"))
        except GateError as e:
            return GateResult(DENY, tool, action, e.code, e.message)

        policies = await self.service.effective_policies(ctx.session_info)
        eff = policies.get(channel_id)
        policy = eff.policy if eff else None
        decision = decide(policy, action)

        # Messages to the agent carry IDs only: channel names are Slack-user-controlled text.
        if decision.kind == DENY:
            if decision.code == "channel_blocked":
                msg = f"Channel {channel_id} is not accessible to this session."
            else:
                msg = (
                    f"Channel {channel_id} is {policy} for this session; sending is not "
                    "permitted. Do not retry; ask the user to change the policy."
                )
            return GateResult(DENY, tool, action, decision.code, msg, channel_id, policy=policy)
        if decision.kind == ALLOW or not with_prompt:
            return GateResult(
                decision.kind, tool, action, channel_id=channel_id, policy=policy
            )

        name = await self.names.channel_name(ctx.adapter, ctx.relay_id, channel_id)
        label = await self.label(ctx, channel_id)
        prompt = {
            "kind": "ask_send" if decision.kind == PROMPT else "choose_policy",
            "tool": tool,
            "action": action,
            "channel_id": channel_id,
            "channel_name": name,
            "channel_label": label,
            "policy": policy,
            "text_preview": self._preview(tool, args),
        }
        return GateResult(
            decision.kind, tool, action, channel_id=channel_id, channel_name=name,
            channel_label=label, policy=policy, prompt=prompt,
        )

    async def authorize(
        self, ctx: RelayContext, tool: str, args: dict[str, Any]
    ) -> tuple[GateResult, Grant | None]:
        """Authoritative handler-side check; ``prompt``/``choose_policy`` outcomes must hold a
        one-shot grant, else they fail closed."""
        result = await self.evaluate(ctx.session_id, tool, args, ctx, with_prompt=False)
        # Always consume: a grant for a call that is now plainly allowed (policy promoted by the
        # same prompt) must not linger.
        grant = self.grants.consume(ctx.session_id, tool, args)
        if result.kind in (ALLOW, DENY):
            return result, grant if result.kind == ALLOW else None
        if grant is not None:
            return GateResult(
                ALLOW, tool, result.action, channel_id=result.channel_id, policy=result.policy,
            ), grant
        label = result.channel_id or ""
        if result.kind == PROMPT:
            result.code = "approval_required"
            result.message = (
                f"Relay send to {label} requires approval but this session's permission mode "
                "does not prompt; set the channel to `auto` explicitly or switch permission mode."
            )
        else:
            result.code = "policy_unset"
            result.message = (
                f"Channel {label} has no policy for this session and this session's permission "
                "mode does not prompt for one. Ask the user to set the channel policy "
                "(blocked/read_only/ask/auto)."
            )
        result.kind = DENY
        return result, None
