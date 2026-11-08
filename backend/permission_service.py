"""
PermissionService: permission callback logic extracted from ClaudeWebUI.

Handles creating permission callbacks, tracking pending requests, and
resolving/denying them — independently of the HTTP layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import PermissionUpdate
from claude_agent_sdk.types import PermissionRuleValue

from shared.event_emitter import emit
from shared.event_envelope import QUEUE_SESSION
from shared.event_queue import EventQueue
from shared.logging_config import get_logger

from .models.messages import (
    MessageRecord,
    PermissionInfo,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PermissionSuggestion,
)
from .relay.adapters import is_relay_tool
from .session_lifecycle import is_interruptible, is_live
from .session_manager import SessionState

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

logger = logging.getLogger(__name__)
debug_logger = get_logger('sdk_debug', category='PERMISSION_CALLBACK')


class PermissionService:
    """Manages permission callback lifecycle for Claude SDK tool approvals."""

    def __init__(
        self,
        coordinator: SessionCoordinator,
        session_queues: dict[str, EventQueue],
    ):
        self.coordinator = coordinator
        self.session_queues = session_queues
        self.pending_permissions: dict[str, asyncio.Future] = {}
        # Issue #1746: request_ids currently open per session, so resolving one
        # agent's permission doesn't clear another still-open agent's PAUSED
        # state on the same session.
        self.pending_by_session: dict[str, set[str]] = {}
        # Issue #2353: the relay tool handler raises its own prompts through this service.
        broker = getattr(getattr(coordinator, "relay", None), "broker", None)
        if broker is not None:
            broker.attach(coordinator)

    def create_permission_callback(self, session_id: str) -> Callable:
        """Create permission callback for tool usage"""
        async def permission_callback(tool_name: str, input_params: dict, context: Any) -> dict:

            # Generate unique request ID to correlate request/response
            request_id = str(uuid.uuid4())

            # Issue #953: Extract tool_use_id and agent_id from SDK v0.1.52+ context
            tool_use_id = getattr(context, 'tool_use_id', None) if context else None
            agent_id = getattr(context, 'agent_id', None) if context else None
            # Issue #1302: Extract enriched context fields from SDK 0.1.74+
            decision_reason = getattr(context, 'decision_reason', None) if context else None
            blocked_path    = getattr(context, 'blocked_path', None) if context else None
            title           = getattr(context, 'title', None) if context else None
            display_name    = getattr(context, 'display_name', None) if context else None
            description     = getattr(context, 'description', None) if context else None

            debug_logger.info(
                f"PERMISSION CALLBACK TRIGGERED: tool={tool_name}, session={session_id}, "
                f"request_id={request_id}, tool_use_id={tool_use_id}, agent_id={agent_id}"
            )
            debug_logger.info(f"Permission requested for tool: {tool_name} (request_id: {request_id})")

            # Issue #2103: relay tools. Reads and `auto` sends are allowed here, blocked/read_only
            # sends are denied, everything else falls through into the normal prompt flow with
            # `relay_prompt` metadata. The tool handler re-checks policy (authoritative gate).
            relay_bridge = None
            relay_gate_result = None
            if is_relay_tool(tool_name):
                relay_bridge = getattr(self.coordinator, "relay_bridge", None)
                if relay_bridge is None:
                    logger.error("Relay bridge unavailable; denying %s", tool_name)
                    return {"behavior": "deny", "message": "Relay policy check unavailable."}
            if relay_bridge is not None:
                try:
                    relay_gate_result = await relay_bridge.pre_hook(
                        session_id, tool_name, input_params
                    )
                except Exception:
                    logger.exception("Relay pre-hook failed; denying %s", tool_name)
                    return {"behavior": "deny", "message": "Relay policy check failed."}
                if relay_gate_result.kind == "allow":
                    return {"behavior": "allow"}
                if relay_gate_result.kind == "deny":
                    # Same message + structured JSON as the handler's refusal (issue #2355)
                    return {"behavior": "deny", "message": relay_gate_result.refusal_text()}

            # Issue #403: Auto-approve Read tool for user-uploaded files
            if tool_name == 'Read':
                file_path = input_params.get('file_path', '')
                if file_path and self.coordinator.is_uploaded_file(session_id, file_path):
                    debug_logger.info(f"Auto-approving Read for uploaded file: {file_path}")
                    return {"behavior": "allow"}

            # Extract suggestions from context
            suggestions = []
            if context and hasattr(context, 'suggestions'):
                for s in context.suggestions:
                    if hasattr(s, 'to_dict'):
                        suggestions.append(s.to_dict())
                    else:
                        suggestions.append(s)
                debug_logger.info(f"Permission context has {len(suggestions)} suggestions")
            if relay_gate_result is not None:
                suggestions = []  # relay approvals never persist allow rules (handler gate)

            return await self.request_decision(
                session_id, tool_name, input_params,
                request_id=request_id,
                tool_use_id=tool_use_id,
                agent_id=agent_id,
                context_fields={
                    "decision_reason": decision_reason,
                    "blocked_path": blocked_path,
                    "title": title,
                    "display_name": display_name,
                    "description": description,
                },
                suggestions=suggestions,
                relay_gate_result=relay_gate_result,
                relay_bridge=relay_bridge,
            )

        return permission_callback

    async def request_decision(
        self,
        session_id: str,
        tool_name: str,
        input_params: dict,
        *,
        request_id: str | None = None,
        tool_use_id: str | None = None,
        agent_id: str | None = None,
        context_fields: dict[str, Any] | None = None,
        suggestions: list[dict] | None = None,
        relay_gate_result: Any = None,
        relay_bridge: Any = None,
        relay_mint_grant: bool = True,
        timeout: float | None = None,
        allow_standalone_card: bool = False,
        deny_card_on_cancel: bool = False,
    ) -> dict:
        """Raise one permission prompt and wait for the owner's decision (issue #2353).

        The prompt body of the SDK permission callback, shared with callers that prompt from
        elsewhere (the relay tool handler). Records the request, flips the tool call to
        awaiting_permission, pauses the session, awaits the Future that the REST resolve
        route completes, persists the response and returns the SDK permission response.

        Relay handler extensions (issue #2353), all off by default so the SDK callback keeps
        its exact behavior: ``timeout`` bounds the wait (deny with ``reason="timeout"``);
        ``allow_standalone_card`` creates a synthetic ``relay-prompt-{request_id}`` tool call
        when the real card cannot be found (instead of auto-denying) and finalises it on every
        outcome; ``relay_mint_grant=False`` keeps the relay answer from minting a one-shot
        grant; ``deny_card_on_cancel`` marks the card denied when the waiting task is cancelled.
        Deny responses carry a ``reason`` (timeout, interrupted, terminated, policy_forbids).
        """
        request_id = request_id or str(uuid.uuid4())
        request_time = time.time()
        context_fields = context_fields or {}
        decision_reason = context_fields.get("decision_reason")
        blocked_path = context_fields.get("blocked_path")
        title = context_fields.get("title")
        display_name = context_fields.get("display_name")
        description = context_fields.get("description")
        suggestions = suggestions or []
        standalone = False

        # Store permission request message using dataclass (Phase 0, Issue #310)
        try:
            # Convert suggestions to dataclass format
            suggestion_objects = [
                PermissionSuggestion.from_dict(s) if isinstance(s, dict) else s
                for s in suggestions
            ]

            # Create permission request dataclass
            permission_request = PermissionRequestMessage(
                request_id=request_id,
                tool_name=tool_name,
                input_params=input_params,
                suggestions=suggestion_objects,
                timestamp=request_time,
                session_id=session_id,
                tool_use_id=tool_use_id,       # Issue #953
                agent_id=agent_id,              # Issue #953
                decision_reason=decision_reason, # Issue #1302
                blocked_path=blocked_path,       # Issue #1302
                title=title,                     # Issue #1302
                display_name=display_name,       # Issue #1302
                description=description,         # Issue #1302
            )

            # Issue #2084 (stage 3-B, §5): the flat PermissionRequestMessage dict is the
            # one triggering_message fed to both the ToolCallUpdate storage write (via
            # update_tool_call_permission_request below) and the live tool_call
            # emission (via MessageRecord.from_tool_call below) — no more StoredMessage
            # wrapper, and no more two independently-built tool_call_data shapes.
            request_dict = permission_request.to_dict()

            # Issue #324: Update ToolCall to awaiting_permission and emit unified tool_call message
            try:
                # Issue #953: O(1) lookup by tool_use_id (always present — SDK
                # pinned well above the v0.1.52 floor that introduced it).
                tool_call = None
                if tool_use_id:
                    tool_call = self.coordinator.get_tool_call_by_id(session_id, tool_use_id)
                    if not tool_call:
                        # Tool call not yet tracked — short wait for async creation
                        event = self.coordinator.get_tool_call_event(session_id)
                        event.clear()
                        try:
                            await asyncio.wait_for(event.wait(), timeout=2.0)
                        except TimeoutError:
                            pass
                        tool_call = self.coordinator.get_tool_call_by_id(session_id, tool_use_id)

                if not tool_call and allow_standalone_card:
                    # Issue #2353: degraded correlation (no hook entry, no tracker match) —
                    # show the prompt on a synthetic card rather than denying the send.
                    tool_use_id = f"relay-prompt-{request_id}"
                    standalone = True
                    request_dict["tool_use_id"] = tool_use_id
                    tool_call = self.coordinator.create_tool_call(
                        session_id, tool_use_id, tool_name, input_params, requires_permission=True
                    )
                    debug_logger.warning(
                        f"[PERMISSIONS] No tool card for {tool_name} in session {session_id}; "
                        f"showing standalone prompt card {tool_use_id}"
                    )

                if tool_call:
                    # Issue #1694: Wait for the assistant message envelope that produced
                    # this tool_call to be queued before appending the awaiting_permission
                    # update, so the frontend always sees the assistant bubble ahead of its
                    # permission prompt. Fail-open on timeout: this barrier only affects
                    # display ordering, not tool execution correctness, so we log a warning
                    # and proceed rather than risk a spurious deny. No-op (zero behavior
                    # change) for any ToolCall built without turn_id.
                    if tool_call.turn_id and not self.coordinator.is_assistant_message_emitted(
                        session_id, tool_call.turn_id
                    ):
                        event = self.coordinator.get_message_emitted_event(session_id)
                        loop = asyncio.get_running_loop()
                        deadline = loop.time() + 2.0  # 2-second fail-open timeout
                        while True:
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                break
                            # Clear before check+wait to avoid missing a signal that
                            # arrives between our check and the wait() call.
                            event.clear()
                            if self.coordinator.is_assistant_message_emitted(
                                session_id, tool_call.turn_id
                            ):
                                break
                            try:
                                await asyncio.wait_for(event.wait(), timeout=remaining)
                            except TimeoutError:
                                break
                            if self.coordinator.is_assistant_message_emitted(
                                session_id, tool_call.turn_id
                            ):
                                break

                        # Fail open regardless of which branch above ended the wait —
                        # this barrier only affects display ordering, never tool
                        # execution correctness, so we log and proceed rather than deny.
                        if not self.coordinator.is_assistant_message_emitted(
                            session_id, tool_call.turn_id
                        ):
                            debug_logger.warning(
                                f"[PERMISSIONS] Assistant message envelope for "
                                f"turn_id {tool_call.turn_id} not emitted after "
                                f"2.0s in session {session_id}. Proceeding anyway "
                                f"(fail-open; affects display order only)."
                            )

                    # Create PermissionInfo from suggestions
                    permission_info = PermissionInfo(
                        message=f"Allow {tool_name}?",
                        suggestions=[s.to_dict() for s in suggestion_objects],
                        risk_level="medium",
                        decision_reason=decision_reason, # Issue #1302
                        blocked_path=blocked_path,       # Issue #1302
                        title=title,                     # Issue #1302
                        display_name=display_name,       # Issue #1302
                        description=description,         # Issue #1302
                        relay_prompt=(
                            relay_gate_result.prompt if relay_gate_result else None
                        ),                               # Issue #2103
                    )

                    # Update tool call status
                    updated_tool_call = self.coordinator.update_tool_call_permission_request(
                        session_id,
                        tool_call.tool_use_id,
                        permission_info,
                        request_id=request_id,
                        triggering_message=request_dict,  # Issue #494: embed permission request data
                    )

                    if updated_tool_call:
                        # Emit unified tool_call message — request_id promoted onto the
                        # flat payload automatically by MessageRecord.from_tool_call (§1).
                        tool_call_data = MessageRecord.from_tool_call(
                            updated_tool_call, triggering_message=request_dict
                        ).to_dict()
                        # _triggering_message is a storage-only embedding — never
                        # previously reached the frontend live; strip it before emitting.
                        tool_call_data.pop("_triggering_message", None)

                        if session_id in self.session_queues:
                            emit(
                                self.session_queues[session_id], QUEUE_SESSION, "tool_call",
                                {
                                    "session_id": session_id,
                                    "data": tool_call_data,
                                    "timestamp": datetime.now(UTC).isoformat(),
                                },
                                scope=session_id, backend_id=self.coordinator.tenant_context.backend_id,
                            )
                        debug_logger.info(f"Appended tool_call awaiting_permission for {tool_name} in session {session_id}")
                else:
                    # Issue #616: No ToolCall found — auto-deny to prevent deadlock
                    debug_logger.warning(
                        f"No ToolCall found for permission request: "
                        f"{tool_name} in session {session_id}. Auto-denying."
                    )
                    return {"behavior": "deny"}

                # Issue #491: Legacy permission_request broadcast removed.
                # Only unified tool_call messages are emitted for permission lifecycle.
            except Exception:
                logger.exception("Failed to broadcast permission request to WebSocket")
                # Issue #616: If broadcast failed, auto-deny rather than deadlock
                return {"behavior": "deny"}

        except Exception:
            logger.exception("Failed to store permission request message")
            return {"behavior": "deny"}

        # Issue #616: Session pause, Future creation, and await are now INSIDE the
        # successful tool_call path. We only reach here if the permission prompt was
        # successfully broadcast to the frontend.

        # Issue #1746: Track this request in the per-session open set before
        # pausing, so concurrent requests on the same session only pause
        # once and only resume the session once every sibling request has
        # resolved.
        session_pending = self.pending_by_session.setdefault(session_id, set())
        is_first_pending_for_session = not session_pending
        session_pending.add(request_id)

        # Set session state to PAUSED while waiting for permission response
        # This provides visual feedback that the session is blocked on user input
        try:
            if is_first_pending_for_session:
                # Issue #2184: pause_session() now fires the "pause" lifecycle-record
                # callback itself (SessionCoordinator._on_session_manager_lifecycle_record),
                # replacing the manual recorder.record_lifecycle("pause") call this used
                # to make here.
                await self.coordinator.session_manager.pause_session(session_id)
                debug_logger.info(f"Set session {session_id} to PAUSED state while waiting for permission")
        except Exception:
            logger.exception(f"Failed to pause session {session_id} for permission wait")

        # Wait for user permission decision via WebSocket
        debug_logger.info(f"PERMISSION CALLBACK: Creating Future for request_id {request_id}")

        # Create a Future to wait for user response
        permission_future = asyncio.Future()
        self.pending_permissions[request_id] = permission_future

        try:
            # Wait for user decision (no timeout - wait indefinitely, unless a caller bounds it)
            debug_logger.info(f"PERMISSION CALLBACK: Waiting for user decision on request_id {request_id}")
            try:
                if timeout is None:
                    response = await permission_future
                else:
                    response = await asyncio.wait_for(permission_future, timeout)
            except TimeoutError:
                self.pending_permissions.pop(request_id, None)
                response = {
                    "behavior": "deny",
                    "message": "No answer within the time limit",
                    "reason": "timeout",
                }
            debug_logger.info(f"PERMISSION CALLBACK: Received user decision for request_id {request_id}: {response}")

            # Issue #1746: Only restore ACTIVE once no sibling request is
            # still open for this session — resolving one agent's request
            # must not clear another agent's still-open PAUSED state.
            self._discard_pending(session_id, request_id)
            await self._maybe_resume_session(session_id)

            decision = response.get("behavior")
            reasoning = f"User {decision}ed permission"

            # Issue #2103: relay prompt answer -> grant + optional policy promotion
            if decision == "allow" and relay_gate_result is not None:
                response = await relay_bridge.apply_response(
                    session_id, tool_name, input_params, relay_gate_result, response,
                    mint_grant=relay_mint_grant,
                )
                decision = response.get("behavior")
                reasoning = f"User {decision}ed permission"

            # Handle permission updates if user chose to apply suggestions
            if decision == "allow" and response.get("apply_suggestions") and suggestions:
                updated_permissions = []
                applied_updates_for_storage = []

                # Use selected_suggestions if provided (granular selection),
                # otherwise fall back to full suggestions list (backward compatibility)
                suggestions_to_apply = response.get("selected_suggestions", suggestions)

                for suggestion in suggestions_to_apply:
                    # Force destination to 'session' as per requirements
                    suggestion_dict = dict(suggestion)
                    suggestion_dict['destination'] = 'session'

                    # Convert rules from dicts to PermissionRuleValue objects if present
                    rules_param = None
                    if suggestion_dict.get('rules'):
                        rules_param = []
                        for rule_dict in suggestion_dict['rules']:
                            # SDK uses snake_case (tool_name, rule_content) but suggestions come in camelCase
                            rule_obj = PermissionRuleValue(
                                tool_name=rule_dict.get('toolName', ''),
                                rule_content=rule_dict.get('ruleContent') or ""
                            )
                            rules_param.append(rule_obj)

                    update = PermissionUpdate(
                        type=suggestion_dict['type'],
                        mode=suggestion_dict.get('mode'),
                        rules=rules_param,
                        behavior=suggestion_dict.get('behavior'),
                        directories=suggestion_dict.get('directories'),
                        destination='session'
                    )
                    updated_permissions.append(update)
                    applied_updates_for_storage.append(suggestion_dict)

                    # Issue #630: Persist addDirectories to session configuration
                    if suggestion_dict['type'] == 'addDirectories' and suggestion_dict.get('directories'):
                        try:
                            await self.coordinator.session_manager.update_additional_directories(
                                session_id, suggestion_dict['directories']
                            )
                            debug_logger.info(
                                f"Updated session {session_id} additional_directories "
                                f"with {len(suggestion_dict['directories'])} dirs"
                            )
                        except Exception:
                            logger.exception("Failed to update session directories")

                response['updated_permissions'] = updated_permissions
                response['applied_updates_for_storage'] = applied_updates_for_storage
                debug_logger.info(f"Built {len(updated_permissions)} permission updates from suggestions")

                # Issue #631: Audit log when user edits permission suggestion text
                if response.get("selected_suggestions"):
                    for sg_applied in suggestions_to_apply:
                        if sg_applied.get('type') != 'addRules':
                            continue
                        for rule in sg_applied.get('rules') or []:
                            tool_name_applied = rule.get('toolName', '')
                            rule_content_applied = rule.get('ruleContent', '')
                            applied_text = (
                                f"{tool_name_applied}({rule_content_applied})"
                                if rule_content_applied else tool_name_applied
                            )
                            # Check if this rule differs from any original suggestion
                            for orig_sg in suggestions:
                                if orig_sg.get('type') != 'addRules':
                                    continue
                                for orig_rule in orig_sg.get('rules') or []:
                                    orig_tool = orig_rule.get('toolName', '')
                                    orig_content = orig_rule.get('ruleContent', '')
                                    orig_text = (
                                        f"{orig_tool}({orig_content})"
                                        if orig_content else orig_tool
                                    )
                                    if orig_tool == tool_name_applied and orig_text != applied_text:
                                        debug_logger.info(
                                            f"Permission edited by user: "
                                            f"original='{orig_text}' → edited='{applied_text}' "
                                            f"in session {session_id}"
                                        )

                # Issue #433: Persist approved tool names to session allowed_tools
                # SDK suggestions split tool spec into toolName + ruleContent,
                # e.g. toolName="Bash", ruleContent="gh issue view:*"
                # Reconstruct full format: "Bash(gh issue view:*)"
                tools_to_persist = set()
                for suggestion_dict in applied_updates_for_storage:
                    if (
                        suggestion_dict.get('type') == 'addRules'
                        and suggestion_dict.get('behavior') == 'allow'
                    ):
                        for rule in suggestion_dict.get('rules') or []:
                            tool_name_r = rule.get('toolName', '')
                            rule_content = rule.get('ruleContent', '')
                            if tool_name_r:
                                if rule_content:
                                    tools_to_persist.add(f"{tool_name_r}({rule_content})")
                                else:
                                    tools_to_persist.add(tool_name_r)

                if tools_to_persist:
                    try:
                        await self.coordinator.session_manager.update_allowed_tools(
                            session_id, list(tools_to_persist)
                        )
                        debug_logger.info(
                            f"Persisted {len(tools_to_persist)} approved tools to session "
                            f"{session_id} allowed_tools: {tools_to_persist}"
                        )
                    except Exception:
                        logger.exception("Failed to persist approved tools")

        except Exception as e:
            # Handle any errors (e.g., session termination)
            logger.exception("PERMISSION CALLBACK: Error waiting for permission decision")
            decision = "deny"
            reasoning = f"Permission request failed: {str(e)}"
            response = {
                "behavior": "deny",
                "message": reasoning
            }

            # Clean up the pending permission
            self.pending_permissions.pop(request_id, None)
            # Issue #1746: This path bypasses resolve() entirely (e.g. session
            # termination while awaiting), so the per-session open set must be
            # cleaned up here too — otherwise a session can be left stuck PAUSED
            # forever if this was its last outstanding request.
            self._discard_pending(session_id, request_id)
            await self._maybe_resume_session(session_id)

        except asyncio.CancelledError:
            # Issue #2353: the waiting handler task was cancelled (CLI cancel, client
            # disconnect): clear the prompt from the card so nothing stays on screen.
            self.pending_permissions.pop(request_id, None)
            if deny_card_on_cancel:
                self._deny_card(session_id, tool_use_id, request_id, tool_name)
            raise

        finally:
            # Issue #1746: asyncio.CancelledError is a BaseException, not caught
            # by `except Exception` above — without this, a coroutine cancelled
            # while awaiting permission_future (e.g. abrupt shutdown) would leak
            # its entry in pending_by_session and could leave the session stuck
            # PAUSED forever. Idempotent with the explicit calls above.
            self._discard_pending(session_id, request_id)
            await self._maybe_resume_session(session_id)

        # Store permission response message using dataclass (Phase 0, Issue #310)
        decision_time = time.time()
        try:
            # Extract clarification message if it was provided
            clarification_msg = response.get("message") if decision == "deny" and not response.get("interrupt", True) else None
            interrupt_flag = response.get("interrupt", True)

            # Convert applied updates to PermissionSuggestion objects
            applied_update_objects = []
            if decision == "allow" and response.get("applied_updates_for_storage"):
                applied_update_objects = [
                    PermissionSuggestion.from_dict(u) if isinstance(u, dict) else u
                    for u in response["applied_updates_for_storage"]
                ]

            # Extract updated_input for AskUserQuestion (contains answers)
            updated_input_data = response.get("updated_input")

            # Create permission response dataclass
            permission_response_msg = PermissionResponseMessage(
                request_id=request_id,
                decision=decision,
                tool_name=tool_name,
                reasoning=reasoning,
                response_time_ms=int((decision_time - request_time) * 1000),
                applied_updates=applied_update_objects,
                clarification_message=clarification_msg,
                interrupt=interrupt_flag,
                timestamp=decision_time,
                session_id=session_id,
                updated_input=updated_input_data,
            )

            # Issue #2084 (stage 3-B, §5): the flat PermissionResponseMessage dict is the
            # one triggering_message fed to both the ToolCallUpdate storage write (via
            # update_tool_call_permission_response below) and the live tool_call
            # emission (via MessageRecord.from_tool_call below) — no more StoredMessage
            # wrapper, and no more two independently-built tool_call_data shapes.
            response_dict = permission_response_msg.to_dict()

            # Issue #324: Update ToolCall after permission response and emit unified tool_call message
            try:
                # Issue #953/#2109: tool_use_id first, then request_id — no
                # name+status fallback.
                tool_call = None
                if tool_use_id:
                    tool_call = self.coordinator.get_tool_call_by_id(session_id, tool_use_id)
                if not tool_call and request_id:
                    tool_call = self.coordinator.get_tool_call_by_request_id(session_id, request_id)

                if tool_call:
                    # Update tool call with permission decision
                    granted = decision == "allow"
                    # Issue #520: Pass applied_updates so they're stored on the ToolCall
                    applied_updates_dicts = [
                        u.to_dict() if hasattr(u, 'to_dict') else u
                        for u in applied_update_objects
                    ] if applied_update_objects else []
                    updated_tool_call = self.coordinator.update_tool_call_permission_response(
                        session_id,
                        tool_call.tool_use_id,
                        granted,
                        triggering_message=response_dict,  # Issue #494: embed permission response data
                        applied_updates=applied_updates_dicts,
                    )

                    if updated_tool_call:
                        # Emit unified tool_call message — request_id promoted onto the
                        # flat payload automatically by MessageRecord.from_tool_call (§1).
                        tool_call_data = MessageRecord.from_tool_call(
                            updated_tool_call, triggering_message=response_dict
                        ).to_dict()
                        # _triggering_message is a storage-only embedding — never
                        # previously reached the frontend live; strip it before emitting.
                        tool_call_data.pop("_triggering_message", None)

                        if session_id in self.session_queues:
                            emit(
                                self.session_queues[session_id], QUEUE_SESSION, "tool_call",
                                {
                                    "session_id": session_id,
                                    "data": tool_call_data,
                                    "timestamp": datetime.now(UTC).isoformat(),
                                },
                                scope=session_id, backend_id=self.coordinator.tenant_context.backend_id,
                            )
                        debug_logger.info(
                            f"Appended tool_call {'running' if granted else 'denied'} "
                            f"for {tool_name} in session {session_id}"
                        )
                        if standalone and granted:
                            # No tool_result will ever arrive for a synthetic card.
                            done = self.coordinator.update_tool_call_result(
                                session_id, tool_call.tool_use_id, "Approved from the relay prompt"
                            )
                            if done is not None:
                                self._emit_tool_call(session_id, done)
            except Exception:
                logger.exception("Failed to update ToolCall for permission response")

            # Issue #491: Legacy permission_response broadcast removed.
            # Only unified tool_call messages are emitted for permission lifecycle.

        except Exception:
            logger.exception("Failed to store permission response message")

        debug_logger.info(f"Permission {decision} for tool: {tool_name} (request_id: {request_id})")
        return response

    async def abandon_request(
        self, session_id: str, tool_use_id: str | None, request_id: str, tool_name: str
    ) -> None:
        """Clean up after a prompt whose waiting task was cancelled, whatever point it reached
        (issue #2353): pending entry, open-request bookkeeping, PAUSED state and the card
        (including a synthetic ``relay-prompt-{request_id}`` one). Idempotent."""
        self.pending_permissions.pop(request_id, None)
        self._discard_pending(session_id, request_id)
        self._deny_card(session_id, tool_use_id or f"relay-prompt-{request_id}", request_id, tool_name)
        await self._maybe_resume_session(session_id)

    async def can_prompt(self, session_id: str) -> tuple[bool, str | None]:
        """Whether anyone can answer a prompt for this session (issue #2353).

        False (with a reason) for sessions that run unattended: the launched SDK was told to
        deny prompts, the session is ephemeral, it has no permission callback (scheduler
        launches pass none) or it is no longer running. Permission *mode* is never consulted.
        """
        info = await self.coordinator.session_manager.get_session_info(session_id)
        if info is None:
            return False, "session_unavailable"
        if info.is_ephemeral:
            return False, "ephemeral_session"
        if not (is_live(info.state) or is_interruptible(info.state)):  # ACTIVE / STARTING / PAUSED
            return False, "session_not_running"
        sdk = self.coordinator._active_sdks.get(session_id)
        if sdk is None:
            return False, "session_not_running"
        if getattr(sdk, "deny_unattended_permission_prompts", False):
            return False, "deny_unattended_prompts"
        if getattr(sdk, "permission_callback", None) is None:
            return False, "no_permission_callback"
        return True, None

    def _emit_tool_call(self, session_id: str, tool_call: Any, triggering_message: dict | None = None) -> None:
        """Emit one unified tool_call event for *tool_call* (issue #2353 helper)."""
        data = MessageRecord.from_tool_call(tool_call, triggering_message=triggering_message).to_dict()
        data.pop("_triggering_message", None)
        if session_id in self.session_queues:
            emit(
                self.session_queues[session_id], QUEUE_SESSION, "tool_call",
                {
                    "session_id": session_id,
                    "data": data,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                scope=session_id, backend_id=self.coordinator.tenant_context.backend_id,
            )

    def _deny_card(
        self, session_id: str, tool_use_id: str | None, request_id: str, tool_name: str
    ) -> None:
        """Best effort: mark the card of an abandoned prompt denied so no prompt stays on it."""
        try:
            tool_call = None
            if tool_use_id:
                tool_call = self.coordinator.get_tool_call_by_id(session_id, tool_use_id)
            if tool_call is None:
                tool_call = self.coordinator.get_tool_call_by_request_id(session_id, request_id)
            if tool_call is None:
                return
            denied = self.coordinator.update_tool_call_permission_response(
                session_id, tool_call.tool_use_id, False
            )
            if denied is not None:
                self._emit_tool_call(session_id, denied)
        except Exception:
            logger.exception("Failed to clear abandoned prompt card for %s", tool_name)

    def _discard_pending(self, session_id: str, request_id: str) -> None:
        """Remove request_id from the per-session open-request set (issue #1746)."""
        session_pending = self.pending_by_session.get(session_id)
        if session_pending is None:
            return
        session_pending.discard(request_id)
        if not session_pending:
            self.pending_by_session.pop(session_id, None)

    def open_permission_count(self, session_id: str) -> int:
        """Number of currently-open permission requests for a session (issue #1746)."""
        return len(self.pending_by_session.get(session_id, ()))

    async def _maybe_resume_session(self, session_id: str) -> None:
        """Restore session to ACTIVE only if no permission requests remain open.

        Issue #1746: previously this ran unconditionally whenever *any* request
        resolved, so resolving one agent's permission could clear another
        agent's still-open "needs attention" PAUSED state on the same session.

        Called from both the happy path and the finally block of the same
        permission_callback invocation (issue #1746's cancellation-safety design) —
        the PAUSED pre-check keeps the second call a true no-op rather than an extra
        (harmless but pointless) resolve_permission_closed() round trip.
        """
        if self.open_permission_count(session_id) != 0:
            return
        try:
            session_info = await self.coordinator.session_manager.get_session_info(session_id)
            if not session_info or session_info.state != SessionState.PAUSED:
                return
            resumed = await self.coordinator.session_manager.resolve_permission_closed(session_id)
            if not resumed:
                return
            # resolve_permission_closed() returns True for its own no-op case too (the
            # session wasn't PAUSED by the time it acquired its lock — e.g. a
            # concurrent error/interrupt moved it to ERROR/TERMINATING between the
            # pre-check above and here). Re-check the actual resulting state before
            # logging a resume that may not have happened — the "resume" lifecycle
            # record itself is handled automatically (issue #2184: resolve_permission_
            # closed() only fires its lifecycle-record callback when a real transition
            # occurred, not on this no-op path).
            post_info = await self.coordinator.session_manager.get_session_info(session_id)
            if post_info and post_info.state == SessionState.ACTIVE:
                debug_logger.info(f"Restored session {session_id} to ACTIVE state after permission decision")
        except Exception:
            logger.exception(f"Failed to restore session {session_id} to ACTIVE after permission")

    def cleanup_pending_for_session(self, session_id: str) -> None:
        """Clean up pending permissions for a specific session by auto-denying them"""
        try:
            # Issue #1754: Scope to this session's own open requests via
            # pending_by_session (added in #1746) instead of walking every
            # session's pending_permissions — otherwise terminating one session
            # auto-denies every other active session's in-flight permission prompts.
            request_ids = list(self.pending_by_session.get(session_id, ()))

            for request_id in request_ids:
                future = self.pending_permissions.pop(request_id, None)
                if future and not future.done():
                    # Auto-deny the permission
                    response = {
                        "behavior": "deny",
                        "message": f"Session {session_id} terminated - auto-denying pending permission",
                        "reason": "terminated",
                    }
                    future.set_result(response)
                    debug_logger.info(f"Auto-denied pending permission {request_id} due to session {session_id} termination")

            if request_ids:
                debug_logger.info(f"Cleaned up {len(request_ids)} pending permissions for session {session_id}")

            self.pending_by_session.pop(session_id, None)
            self._clear_relay_session_state(session_id)

        except Exception:
            logger.exception(f"Error cleaning up pending permissions for session {session_id}")

    def _clear_relay_session_state(self, session_id: str) -> None:
        """Drop a session's one-shot relay grants and hook-correlation entries (issue #2353)."""
        relay = getattr(self.coordinator, "relay", None)
        if relay is None:
            return
        try:
            relay.gate.grants.clear_session(session_id)
            relay.broker.clear_session(session_id)
        except Exception:
            logger.exception("Failed to clear relay state for session %s", session_id)

    async def deny_for_session(self, session_id: str) -> None:
        """Deny this session's own pending permission request(s) due to an interrupt.

        Issue #2159 (UC4): replaces the old deny_all_for_interrupt(), which denied
        every session's pending permissions service-wide — interrupting one PAUSED
        session used to nuke every other session's in-flight permission prompts too.
        Scoped via pending_by_session (issue #1746), same as cleanup_pending_for_session,
        but additionally resumes this session to ACTIVE once its requests are cleared —
        termination doesn't need that (the session is going away regardless), an
        interrupt does.
        """
        request_ids = list(self.pending_by_session.get(session_id, ()))
        for request_id in request_ids:
            future = self.pending_permissions.pop(request_id, None)
            if future and not future.done():
                future.set_result({
                    "behavior": "deny",
                    "message": "Permission denied due to session interrupt",
                    "interrupt": True,
                    "reason": "interrupted",
                })
                debug_logger.info(
                    f"Denied pending permission {request_id} due to interrupt of session {session_id}"
                )
            self._discard_pending(session_id, request_id)
        await self._maybe_resume_session(session_id)

    def resolve(self, request_id: str, response: dict) -> bool:
        """Resolve a pending permission request.

        Returns True on success, False if request_id not found or Future already done.
        """
        pending_future = self.pending_permissions.pop(request_id, None)
        if pending_future is None:
            return False
        if pending_future.done():
            return False
        pending_future.set_result(response)
        return True
