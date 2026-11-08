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
from .adapters import SPECS, AdapterSpec, get_spec, parse_relay_tool
from .directory import DirectoryLookupError, RelayDirectory
from .grants import Grant, GrantStore
from .names import NameResolver
from .policy import ALLOW, DENY, PROMPT
from .safety import contains_trigger_syntax, safe_error_code, wrap_untrusted
from .selfdm import SelfDmResolver
from .slack_policy import CHANNEL_ID_RE

if TYPE_CHECKING:
    from backend.relay_manager import RelayManager
    from backend.session_manager import SessionInfo, SessionManager

    from .service import RelayService

# U… (and Enterprise Grid W…) are user IDs, never conversation IDs: they resolve to a DM.
_USER_ID_RE = re.compile(r"^@?([UW][A-Z0-9]{6,})\Z")
_NAME_RE = re.compile(r"^#?([a-z0-9][a-z0-9._-]{0,79})\Z", re.IGNORECASE)
_PERSON_NAME_RE = re.compile(r"^@\s*(\S.{0,79})\Z")
# Name-resolution failures the permission pre-hook lets through, so the (authoritative) handler
# can return the full structured refusal, including the fenced candidate list.
NAME_LOOKUP_CODES = frozenset({"name_ambiguous", "name_not_found"})
PREVIEW_LEN = 40000  # the prompt shows everything that will be sent (bodies are capped at 35000)


def tool_short_name(tool_name: str) -> str | None:
    """``mcp__relay_slack__post`` -> ``post``; None for non-relay tools."""
    parsed = parse_relay_tool(tool_name)
    return parsed[1] if parsed and parsed[1] in parsed[0].tool_actions else None


class GateError(Exception):
    def __init__(self, code: str, message: str, candidates: list[dict[str, Any]] | None = None):
        self.code = code
        self.message = message
        self.candidates = candidates  # Slack-controlled; only ever returned inside the fence
        super().__init__(message)


@dataclass
class RelayContext:
    session_id: str
    relay_id: str
    spec: AdapterSpec
    session_info: SessionInfo
    adapter: RelayAdapter
    handle: str | None
    owner_user_id: str | None
    footer_enabled: bool
    footer_template: str | None


@dataclass
class SessionlessContext:
    """Directory context without a session (settings UI): relay, spec and adapter only."""

    relay_id: str
    spec: AdapterSpec
    adapter: RelayAdapter
    owner_user_id: str | None


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
    candidates: list[dict[str, Any]] | None = None

    def error_result(self) -> dict[str, Any]:
        return error_result(
            self.code or "denied", self.message or "Not permitted.", self.channel_id,
            self.action or self.tool, self.policy, self.candidates,
        )


def error_result(
    code: str, message: str, channel: str | None, action: str, policy: str | None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured policy refusal: human text block + machine-readable JSON block (+ a fenced
    candidate list for an ambiguous name)."""
    detail = {"error": "relay_policy", "code": code, "channel": channel, "action": action,
              "policy": policy}
    content = [
        {"type": "text", "text": message},
        {"type": "text", "text": json.dumps(detail)},
    ]
    if candidates:
        content.append({"type": "text", "text": wrap_untrusted(candidates)})
    return {"content": content, "is_error": True}


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
        self.directory = RelayDirectory(self.names, clock=clock)
        self.selfdm = SelfDmResolver(relay_manager)

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
        spec = get_spec(getattr(self.relay_manager.relays[relay_id], "adapter_type", None))
        if spec is None:
            return None
        enabled = attachment["relay_footer_enabled"]
        return RelayContext(
            session_id=session_id,
            relay_id=relay_id,
            spec=spec,
            session_info=info,
            adapter=self.relay_manager.adapter_for(relay_id),
            handle=info.config.get("relay_handle") or await self.service.ensure_handle(session_id),
            owner_user_id=getattr(self.relay_manager.relays[relay_id], "owner_user_id", None),
            footer_enabled=True if enabled is None else bool(enabled),
            footer_template=attachment["relay_footer_template"],
        )

    def relay_context(self, relay_id: str) -> SessionlessContext | None:
        """Session-less context for directory browsing; None when the relay is missing, has no
        user token or its adapter type is unknown."""
        record = self.relay_manager.relays.get(relay_id)
        if record is None or not self.relay_manager.has_token(relay_id, "user"):
            return None
        spec = get_spec(getattr(record, "adapter_type", None))
        if spec is None:
            return None
        return SessionlessContext(
            relay_id, spec, self.relay_manager.adapter_for(relay_id),
            getattr(record, "owner_user_id", None),
        )

    # ------------------------------------------------------- channel handling

    async def resolve_channel(self, ctx: RelayContext, ref: Any) -> str:
        """Resolve an ID, ``#name``, ``@name`` or user ID (DM) to a canonical conversation ID.

        Names match exactly (case/diacritic-insensitive): ambiguity returns candidates, never a
        guess. Always runs before any policy check.
        """
        if not isinstance(ref, str) or not ref.strip():
            raise GateError("channel_unresolved", "A 'channel' argument is required.")
        ref = ref.strip()
        user = _USER_ID_RE.match(ref)
        if CHANNEL_ID_RE.match(ref) and not user:
            return ref
        person = _PERSON_NAME_RE.match(ref)
        try:
            if user:
                try:
                    return await ctx.adapter.open_dm(user.group(1))
                except RelayError as e:
                    # '@UNIVERSE' looks like an ID but may be a name: only then try the directory
                    if not (person and e.code in ("user_not_found", "invalid_users")):
                        raise
            if person:
                return await ctx.adapter.open_dm(
                    await self.directory.resolve_user(ctx, person.group(1))
                )
            named = _NAME_RE.match(ref)
            if named:
                return await self.directory.resolve_channel(ctx, named.group(1))
        except DirectoryLookupError as e:
            raise GateError(e.code, e.message, e.candidates) from None
        except RelayError as e:
            raise GateError("channel_unresolved", f"Could not resolve channel ({safe_error_code(e.code)}).") from None
        raise GateError(
            "channel_unresolved",
            "Could not resolve the channel. Use a channel ID (C…/G…/D…), #channel-name, a user "
            "ID (U…) or @name to message a person (find_channels searches by name).",
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
        parsed = parse_relay_tool(tool_name)
        tool = parsed[1] if parsed else tool_name
        ctx = ctx or await self.context(session_id)
        spec = parsed[0] if parsed else (ctx.spec if ctx else None)
        if spec is None:  # bare tool name and no relay attached: not-attached beats unknown
            if any(tool in s.tool_actions for s in SPECS.values()):
                return GateResult(
                    DENY, tool, None, "relay_not_attached",
                    "No relay with a user token is attached to this session.",
                )
            return GateResult(DENY, tool_name, code="unknown_tool", message="Unknown relay tool.")
        if tool not in spec.tool_actions or (ctx is not None and ctx.spec is not spec):
            return GateResult(DENY, tool_name, code="unknown_tool", message="Unknown relay tool.")
        action = spec.tool_actions[tool]
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
            return GateResult(DENY, tool, action, e.code, e.message, candidates=e.candidates)

        policies = await self.service.effective_relay_policies(ctx.session_info, ctx.relay_id)
        eff = policies.get(channel_id)
        policy = eff.policy if eff else None
        decision = ctx.spec.policy_model.decide(policy, action)

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
