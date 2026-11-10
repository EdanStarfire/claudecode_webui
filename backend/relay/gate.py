"""RelayPolicyGate: channel resolution + policy decision for relay tool calls (issue #2103).

One decision path serves both gates: the permission-callback pre-hook (``evaluate``) and the
authoritative tool-handler check (``authorize``). The handler's check is the one that
cannot be bypassed: it reads live policy (the permission mode only decides, in the handler's
prompt step, whether a needed prompt is refused in don't-ask mode), ``ask`` sends need a
one-shot grant from the callback's prompt or an answer to the handler's own prompt, unlisted
channels need a chosen policy, and text containing ``!handle`` trigger syntax is refused
before any network call.
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
from .policy import ALLOW, CHOOSE_POLICY, DENY, PROMPT
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
_PLAIN_RE = re.compile(r"[\x00-\x1f\x7f]+")  # agent-supplied filter text shown in a prompt: one line
SCOPE_PROMPT_TEXT = {
    "mentions": (
        "Let this session read every Slack message that mentions you, in any channel or "
        "conversation you are in? It will receive them as untrusted content. Channels you blocked "
        "stay excluded."
    ),
    "dms": (
        "Let this session read the direct and group messages other people send you? Your own "
        "messages and your self-DM are excluded; it will receive the rest as untrusted content. "
        "Conversations you blocked stay excluded."
    ),
}
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
    prompt_via: str | None = None  # callback | handler: how an approval was obtained (audit)
    approval: str | None = None  # "thread_grant": allowed by an owned instruction thread (3a)

    def error_result(self) -> dict[str, Any]:
        return error_result(
            self.code or "denied", self.message or "Not permitted.", self.channel_id,
            self.action or self.tool, self.policy, self.candidates,
        )

    def refusal_text(self) -> str:
        """The refusal as one string for a permission deny: the handler's message block and
        structured JSON block joined by a newline (issue #2355)."""
        return refusal_text(
            self.code or "denied", self.message or "Not permitted.", self.channel_id,
            self.action or self.tool, self.policy,
        )


def refusal_detail(code: str, channel: str | None, action: str, policy: str | None) -> str:
    """The machine-readable refusal block (shared by the handler result and the pre-hook deny)."""
    return json.dumps({
        "error": "relay_policy", "code": code, "channel": channel, "action": action,
        "policy": policy,
    })


def refusal_text(
    code: str, message: str, channel: str | None, action: str, policy: str | None
) -> str:
    """Human message + structured block as one string: what a permission deny can carry, and
    the same content as ``error_result`` so the agent sees one refusal in every permission mode."""
    return f"{message}\n{refusal_detail(code, channel, action, policy)}"


def error_result(
    code: str, message: str, channel: str | None, action: str, policy: str | None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured policy refusal: human text block + machine-readable JSON block (+ a fenced
    candidate list for an ambiguous name)."""
    content = [
        {"type": "text", "text": message},
        {"type": "text", "text": refusal_detail(code, channel, action, policy)},
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
        # (session_id, relay_id, channel_id, thread_ts) -> bool: the inbound stage's owned
        # instruction threads (issue #2103 stage 3a, D3). None = feature off.
        self.thread_grants = None

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
        if tool == "monitor_thread":
            return f"monitor thread {args.get('thread_ts')}: new replies will be delivered to the session"
        if tool == "monitor_channel":
            keywords = [_PLAIN_RE.sub(" ", str(k)) for k in (
                args.get("keywords") if isinstance(args.get("keywords"), list) else [])]
            users = [_PLAIN_RE.sub(" ", str(u)) for u in (
                args.get("users") if isinstance(args.get("users"), list) else [])]
            filters = "; ".join(p for p in (
                f"keywords: {', '.join(map(str, keywords))}"[:300] if keywords else "",
                f"users: {', '.join(map(str, users))}"[:300] if users else "",
            ) if p)
            return f"monitor channel ({filters or 'no filter'}): matching messages will be delivered to the session"
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
        if tool in ctx.spec.scope_tools:
            return self._scope_decision(ctx, tool, args, with_prompt)
        if action is None:  # search: filtered per channel in the handler
            return GateResult(ALLOW, tool)

        if any(contains_trigger_syntax(t) for t in self.outbound_texts(tool, args)):
            return GateResult(
                DENY, tool, action, "trigger_syntax_refused",
                "Refused: an exclamation mark directly followed by a handle (e.g. !deploy) is not "
                "allowed in Slack messages, because it would be read as an instruction to a "
                "session. Rewrite the text without the '!' directly before the word (write the "
                "handle without '!', or put a space after the '!') and send it again.",
                channel_id=args.get("channel") if isinstance(args.get("channel"), str) else None,
            )
        try:
            channel_id = await self.resolve_channel(ctx, args.get("channel"))
        except GateError as e:
            return GateResult(DENY, tool, action, e.code, e.message, candidates=e.candidates)

        if tool in ctx.spec.monitor_tools and channel_id.startswith("D"):
            # Monitors are never created on DMs here (DMs are a later stage); the self-DM is the
            # instruction channel and is never monitorable, in any thread. Refused before any prompt.
            self_dm = await self.selfdm.resolve(ctx)
            code = "self_dm_not_monitorable" if self_dm == channel_id else "dm_not_monitorable_here"
            msg = (
                f"Conversation {channel_id} cannot be monitored: "
                + ("the user's own self-DM is the instruction channel and is never monitored."
                   if code == "self_dm_not_monitorable"
                   else "direct messages cannot be monitored with this tool.")
            )
            return GateResult(DENY, tool, action, code, msg, channel_id)

        policies = await self.service.effective_relay_policies(ctx.session_info, ctx.relay_id)
        eff = policies.get(channel_id)
        policy = eff.policy if eff else None
        thread_grant = False
        if policy in (None, "ask") and self._owned_thread(ctx, tool, args, channel_id):
            # D3: the owner started this self-DM thread with a valid !handle instruction (or
            # followed up in it): replies there need no prompt. Only an unset / ask policy is
            # upgraded; an explicit blocked / read_only on the self-DM always wins.
            policy, thread_grant = "auto", True
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
                decision.kind, tool, action, channel_id=channel_id, policy=policy,
                approval="thread_grant" if thread_grant and decision.kind == ALLOW else None,
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

    def _scope_decision(
        self, ctx: RelayContext, tool: str, args: dict[str, Any], with_prompt: bool
    ) -> GateResult:
        """Scope monitors (mentions / DMs, inbound 3b-ii) always need the owner's consent: one
        plain-language prompt, no channel resolution and no policy involved (channels the owner
        blocked are excluded later, at delivery). Bypass keeps prompting: this branch never
        reads the permission mode."""
        if not with_prompt:
            return GateResult(PROMPT, tool, "monitor_scope")
        record = self.relay_manager.relays.get(ctx.relay_id)
        scope = "mentions" if tool == "monitor_mentions" else "dms"
        prompt = {
            "kind": "monitor_scope", "tool": tool, "action": "monitor_scope", "scope": scope,
            "filters_summary": self._filters_summary(args),
            "relay_name": _PLAIN_RE.sub(" ", str(getattr(record, "name", "") or ""))[:80],
            "text": SCOPE_PROMPT_TEXT[scope],
        }
        return GateResult(PROMPT, tool, "monitor_scope", prompt=prompt)

    @staticmethod
    def _filters_summary(args: dict[str, Any]) -> str:
        keywords = [_PLAIN_RE.sub(" ", str(k)) for k in (
            args.get("keywords") if isinstance(args.get("keywords"), list) else [])]
        users = [_PLAIN_RE.sub(" ", str(u)) for u in (
            args.get("users") if isinstance(args.get("users"), list) else [])]
        return "; ".join(p for p in (
            f"keywords: {', '.join(keywords)}"[:300] if keywords else "",
            f"users: {', '.join(users)}"[:300] if users else "",
        ) if p)

    def _owned_thread(
        self, ctx: RelayContext, tool: str, args: dict[str, Any], channel_id: str
    ) -> bool:
        """True for reply / post / upload_file with a ``thread_ts``, or read_thread, targeting an
        instruction thread of the CALLING session in the owner's self-DM."""
        lookup = self.thread_grants
        if lookup is None or tool not in ("reply", "post", "upload_file", "read_thread"):
            return False
        thread_ts = args.get("thread_ts")
        if not isinstance(thread_ts, str) or not thread_ts:
            return False
        record = self.relay_manager.relays.get(ctx.relay_id)
        if record is None or not record.self_dm_channel_id or channel_id != record.self_dm_channel_id:
            return False
        if record.owner_user_id != ctx.owner_user_id:
            return False
        return bool(lookup(ctx.session_id, ctx.relay_id, channel_id, thread_ts))

    async def authorize(
        self, ctx: RelayContext, tool: str, args: dict[str, Any]
    ) -> tuple[GateResult, Grant | None]:
        """Authoritative handler-side check, from live policy (the permission mode is not read here; see ``_ask_owner``).

        ``allow`` / ``deny`` are final. A send that needs approval (``prompt`` / ``choose_policy``)
        is allowed when a one-shot grant exists (the permission callback already prompted:
        default / acceptEdits); otherwise the ``prompt`` / ``choose_policy`` result is returned,
        carrying the prompt payload, and the handler asks the owner itself (issue #2353).
        """
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
        # Needs approval and no grant: build the prompt payload for the owner.
        return await self.evaluate(ctx.session_id, tool, args, ctx, with_prompt=True), None

    async def re_authorize(
        self, ctx_session_id: str, tool: str, args: dict[str, Any], *, prompted_kind: str = PROMPT
    ) -> tuple[RelayContext | None, GateResult]:
        """Fresh re-evaluation after an in-handler approval (issue #2353): a new context (relay
        still attached, user token still present, same relay) and a new policy decision, so edits
        made while the prompt waited apply. ``prompt`` / ``choose_policy`` cannot be returned as an
        approval: the owner has just answered, so they are reported as ``allow`` - except that an
        approved ``ask`` send whose channel lost its policy meanwhile is refused (``policy_unset``):
        an unlisted channel needs a chosen policy."""
        ctx = await self.context(ctx_session_id)
        if ctx is None:
            return None, GateResult(
                DENY, tool, None, "relay_not_attached",
                "No relay with a user token is attached to this session.",
            )
        result = await self.evaluate(ctx_session_id, tool, args, ctx, with_prompt=False)
        if result.kind == CHOOSE_POLICY and prompted_kind == PROMPT:
            return ctx, GateResult(
                DENY, tool, result.action, "policy_unset",
                f"Channel {result.channel_id} no longer has a policy; nothing was sent.",
                result.channel_id,
            )
        if result.kind in (PROMPT, CHOOSE_POLICY):
            result = GateResult(
                ALLOW, tool, result.action, channel_id=result.channel_id, policy=result.policy,
            )
        return ctx, result


def approval_refusal(code: str, channel_id: str | None, action: str, policy: str | None = None) -> dict[str, Any]:
    """Agent-facing refusal for an approval that did not end in an allow (IDs only)."""
    texts = {
        "approval_denied": "The owner declined this send. Nothing was sent. Do not retry unless asked.",
        "approval_timeout": "No answer from the owner within 10 minutes; nothing was sent.",
        "approval_unavailable": (
            "No one is available to approve this send (unattended session). Nothing was sent; "
            "ask the user to set the channel to `auto` or run an attended session."
        ),
        "approval_unavailable_dont_ask": (
            "This session is in don't-ask mode, so a send to an `ask` channel is refused instead "
            "of prompting. Nothing was sent. Ask the owner to set the channel to `auto` or to "
            "change the permission mode."
        ),
        "policy_unset_dont_ask": (
            f"Channel {channel_id or ''} has no policy and this session is in don't-ask mode, so "
            "none can be chosen and nothing was sent. Ask the owner to set a policy for the "
            "channel (for example `auto`) or to change the permission mode."
        ),
        "monitor_consent_unavailable": (
            "No one is available to approve this monitor (unattended session). Nothing was "
            "created; ask the user to run it in an attended session."
        ),
        "monitor_consent_unavailable_dont_ask": (
            "This session is in don't-ask mode, so a monitor that needs consent is refused "
            "instead of prompting. Nothing was created; ask the user to change the permission mode."
        ),
        "approval_cancelled": (
            "The session was interrupted or stopped before the owner answered; nothing was sent."
        ),
        "policy_unset": (
            f"Channel {channel_id or ''} has no policy and no one is available to choose one."
        ),
    }
    return error_result(code, texts[code], channel_id, action, policy)
