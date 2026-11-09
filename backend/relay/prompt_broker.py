"""RelayPromptBroker: the approval prompt raised from inside a relay tool call (issue #2353).

The permission callback only runs when the CLI's own mode decides to ask, so in auto and bypass
mode (and for pre-approved rules) a send that needs approval reaches the tool handler with no
grant. The handler's gate is authoritative, so it asks the owner itself through this broker,
over the same prompt machinery (``PermissionService.request_decision``), then re-evaluates
policy before sending. The broker itself never reads the permission mode: the handler refuses
before calling it in don't-ask mode (issue #2360), and a prompt already waiting stays
answerable if the session switches into don't-ask meanwhile.

The prompt is shown on the call's own tool card: its ``tool_use_id`` comes from the
record-only ``PreToolUse`` hook registry; failing that from a scan of the session's tracked
tool calls; failing that a standalone ``relay-prompt-{request_id}`` card (the prompt is still
shown and answered, the original card just stays "running" and is not linked to it).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .call_registry import ClaimedCall, RelayCallRegistry
from .constants import RELAY_PROMPT_TIMEOUT_SECONDS
from .gate import GateResult

if TYPE_CHECKING:
    from backend.session_coordinator import SessionCoordinator

    from .permission_bridge import RelayPermissionBridge

logger = logging.getLogger(__name__)

# BrokerOutcome kinds
ALLOW = "allow"
DENIED = "denied"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
POLICY = "policy"  # the answer led to a policy that forbids this action: re-evaluate for the code
UNAVAILABLE = "unavailable"

TRACKER_WAIT_SECONDS = 2.0  # same short wait the permission callback gives a not-yet-tracked card
_CANCEL_REASONS = frozenset({"interrupted", "terminated", "cancelled"})


@dataclass
class BrokerOutcome:
    kind: str
    approval: str | None = None  # prompt_allow | prompt_promote (kind == allow)
    reason: str | None = None  # why nobody could be asked (kind == unavailable)
    response: dict[str, Any] = field(default_factory=dict)


class RelayPromptBroker:
    def __init__(
        self, registry: RelayCallRegistry, bridge: RelayPermissionBridge,
        *, timeout: float = RELAY_PROMPT_TIMEOUT_SECONDS,
    ) -> None:
        self.registry = registry
        self.bridge = bridge
        self.timeout = timeout
        self._coordinator: SessionCoordinator | None = None
        self._scan_claimed: set[str] = set()  # tool cards bound by an in-flight tracker scan

    def attach(self, coordinator: SessionCoordinator) -> None:
        """Late binding: the coordinator is built after the relay runtime."""
        self._coordinator = coordinator

    def _service(self):
        return getattr(self._coordinator, "permission_service", None) if self._coordinator else None

    async def can_prompt(self, session_id: str) -> tuple[bool, str | None]:
        service = self._service()
        if service is None:
            return False, "no_permission_service"
        return await service.can_prompt(session_id)

    def clear_session(self, session_id: str) -> None:
        self.registry.clear_session(session_id)

    def claim(self, session_id: str, full_name: str, args: dict[str, Any]) -> ClaimedCall | None:
        """The hook-recorded tool_use_id of this call, if any (never raises)."""
        try:
            return self.registry.claim(session_id, full_name, args)
        except Exception:
            logger.exception("Relay call registry claim failed")
            return None

    async def _tool_use_id(
        self, session_id: str, full_name: str, args: dict[str, Any], claimed: ClaimedCall | None
    ) -> tuple[str | None, str | None]:
        """(tool_use_id, agent_id): hook registry entry first, then the tracker scan;
        (None, None) when neither finds the call (standalone card)."""
        if claimed is not None:
            return claimed.tool_use_id, claimed.agent_id
        try:
            found = await self._scan_tracker(session_id, full_name, args)
        except Exception:
            logger.exception("Relay tool call tracker scan failed")
            found = None
        return (found, None) if found else (None, None)

    async def _scan_tracker(
        self, session_id: str, full_name: str, args: dict[str, Any]
    ) -> str | None:
        coordinator = self._coordinator
        if coordinator is None:
            return None

        def scan() -> str | None:
            calls = getattr(coordinator, "_active_tool_calls", {}).get(session_id, {})
            for call in list(calls.values()):
                if (
                    call.name == full_name and call.input == args and call.request_id is None
                    and call.status.value in ("pending", "running")
                    and call.tool_use_id not in self._scan_claimed
                ):
                    self._scan_claimed.add(call.tool_use_id)  # no await since the check: atomic
                    return call.tool_use_id
            return None

        found = scan()
        if found is None:
            event = coordinator.get_tool_call_event(session_id)
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=TRACKER_WAIT_SECONDS)
            except TimeoutError:
                pass
            found = scan()
        return found

    async def request(
        self, session_id: str, gate_result: GateResult, full_name: str, args: dict[str, Any],
        claimed: ClaimedCall | None = None,
    ) -> BrokerOutcome:
        """Prompt the owner and wait (bounded). Never mints a grant."""
        service = self._service()
        if service is None:
            return BrokerOutcome(UNAVAILABLE, reason="no_permission_service")
        request_id = str(uuid.uuid4())
        tool_use_id: str | None = None
        try:
            tool_use_id, agent_id = await self._tool_use_id(session_id, full_name, args, claimed)
            response = await service.request_decision(
                session_id, full_name, args, request_id=request_id,
                tool_use_id=tool_use_id, agent_id=agent_id, suggestions=[],
                relay_gate_result=gate_result, relay_bridge=self.bridge, relay_mint_grant=False,
                timeout=self.timeout, allow_standalone_card=True, deny_card_on_cancel=True,
            )
        except asyncio.CancelledError:
            # cancelled at any point (including before the Future wait): leave no prompt, no
            # pending entry, no PAUSED session and no synthetic card behind
            await service.abandon_request(session_id, tool_use_id, request_id, full_name)
            raise
        finally:
            self._scan_claimed.discard(tool_use_id)
        if response.get("behavior") == "allow":
            return BrokerOutcome(
                ALLOW, approval=response.get("relay_approval") or "prompt_allow", response=response
            )
        reason = response.get("reason")
        if reason == "timeout":
            return BrokerOutcome(TIMEOUT, response=response)
        if reason in _CANCEL_REASONS:
            return BrokerOutcome(CANCELLED, response=response)
        if reason in ("policy_forbids", "not_attached"):
            return BrokerOutcome(POLICY, response=response)
        if reason in ("user_denied", "invalid_choice"):
            return BrokerOutcome(DENIED, response=response)
        # a bare deny: the prompt could not even be raised (no card, broadcast/storage failure)
        return BrokerOutcome(UNAVAILABLE, reason="prompt_not_raised", response=response)
