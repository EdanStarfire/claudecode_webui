"""Permission-callback integration for relay tools (issue #2103).

``pre_hook`` runs at the top of the permission callback for ``mcp__relay_{adapter}__*`` tools:
reads on readable channels and ``auto`` sends are allowed without UI, blocked/read_only
sends are denied, and ``ask`` / unlisted channels fall through into the normal prompt flow
carrying ``relay_prompt`` metadata. ``apply_response`` turns the user's answer
(``relay_choice``) into a one-shot grant plus an optional SESSION-tier policy change; the
same answer handling serves the prompt the tool handler raises itself (issue #2353).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .adapters import is_relay_tool
from .audit import RelayAuditLog
from .gate import NAME_LOOKUP_CODES, GateResult, RelayPolicyGate, tool_short_name
from .policy import ALLOW, DENY

if TYPE_CHECKING:
    from .service import RelayService

logger = logging.getLogger(__name__)


class RelayPermissionBridge:
    def __init__(
        self, gate: RelayPolicyGate, service: RelayService, audit: RelayAuditLog
    ) -> None:
        self.gate = gate
        self.service = service
        self.audit = audit

    @staticmethod
    def handles(tool_name: str) -> bool:
        return is_relay_tool(tool_name)

    async def pre_hook(self, session_id: str, tool_name: str, args: dict[str, Any]) -> GateResult:
        result = await self.gate.evaluate(session_id, tool_name, args)
        short = tool_short_name(tool_name)
        if result.kind == DENY and result.code in NAME_LOOKUP_CODES:
            # Nothing is sent: the handler re-resolves and returns the structured refusal (with
            # the fenced candidate list), which a bare permission deny message cannot carry.
            return GateResult(ALLOW, result.tool, result.action)
        if result.kind == DENY and short is not None:
            ctx = await self.gate.context(session_id)
            if ctx is not None and short in ctx.spec.write_tools:
                await self.audit.record(
                    ctx.relay_id, session_id=session_id, handle=ctx.handle,
                    session_name=ctx.session_info.name, tool=short, channel_id=result.channel_id,
                    channel_name=result.channel_name, thread_ts=args.get("thread_ts"),
                    outcome="denied", code=result.code, policy=result.policy, approval="auto",
                )
        return result

    async def apply_response(
        self,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        gate_result: GateResult,
        response: dict[str, Any],
        *,
        mint_grant: bool = True,
    ) -> dict[str, Any]:
        """Post-process an ``allow`` response to a relay prompt. Returns the response to hand
        the SDK (possibly converted to a deny, with a ``reason``) and records the approval kind
        as ``relay_approval``. ``mint_grant=False`` is the handler-raised prompt (issue #2353):
        the answer is consumed directly, so no one-shot grant is left behind."""
        prompt = gate_result.prompt or {}
        short = tool_short_name(tool_name) or gate_result.tool
        choice = response.get("relay_choice") or {}
        chosen = choice.get("policy")
        promote = bool(choice.get("promote"))
        channel_id = gate_result.channel_id
        action = gate_result.action or "send"
        ctx = await self.gate.context(session_id)
        if ctx is None:
            return {
                "behavior": "deny", "message": "No relay is attached to this session.",
                "reason": "not_attached",
            }
        model = ctx.spec.policy_model

        if chosen is not None and chosen not in model.values:
            return {
                "behavior": "deny", "message": "Invalid relay policy choice.",
                "reason": "invalid_choice",
            }
        approval = "prompt_allow"
        new_policy: str | None = None
        if prompt.get("kind") == "ask_send":
            if promote:
                new_policy = "auto"
        else:  # choose_policy: a policy is mandatory
            if chosen is None:
                return {
                    "behavior": "deny", "message": "No channel policy was chosen.",
                    "reason": "invalid_choice",
                }
            new_policy = chosen
        if new_policy is not None:
            approval = "prompt_promote"
            decision = model.decide(new_policy, action)
            # The audited write path records the change (source=prompt, issue #2355).
            await self.service.set_policy(
                session_id, ctx.relay_id, channel_id, new_policy, source="prompt",
                audit_extra={
                    "tool": gate_result.tool, "channel_name": gate_result.channel_label,
                    "approval": "prompt_promote",
                },
            )
            if decision.kind == DENY:
                return {
                    "behavior": "deny",
                    "message": (
                        f"Channel policy saved as {new_policy}; this {action} is not permitted "
                        "under it."
                    ),
                    "reason": "policy_forbids",
                }
        if mint_grant:
            self.gate.grants.add(session_id, short, args, approval)
        return {**response, "relay_approval": approval}
