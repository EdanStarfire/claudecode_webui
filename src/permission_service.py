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

from .event_queue import EventQueue
from .models.messages import (
    PermissionInfo,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PermissionSuggestion,
    StoredMessage,
)
from .session_manager import SessionState

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

logger = logging.getLogger(__name__)


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

    def create_permission_callback(self, session_id: str) -> Callable:
        """Create permission callback for tool usage"""
        async def permission_callback(tool_name: str, input_params: dict, context: Any) -> dict:

            # Generate unique request ID to correlate request/response
            request_id = str(uuid.uuid4())
            request_time = time.time()

            # Issue #953: Extract tool_use_id and agent_id from SDK v0.1.52+ context
            tool_use_id = getattr(context, 'tool_use_id', None) if context else None
            agent_id = getattr(context, 'agent_id', None) if context else None

            logger.info(
                f"PERMISSION CALLBACK TRIGGERED: tool={tool_name}, session={session_id}, "
                f"request_id={request_id}, tool_use_id={tool_use_id}, agent_id={agent_id}"
            )
            logger.info(f"Permission requested for tool: {tool_name} (request_id: {request_id})")

            # Issue #403: Auto-approve Read tool for user-uploaded files
            if tool_name == 'Read':
                file_path = input_params.get('file_path', '')
                if file_path and self.coordinator.is_uploaded_file(session_id, file_path):
                    logger.info(f"Auto-approving Read for uploaded file: {file_path}")
                    return {"behavior": "allow"}

            # Extract suggestions from context
            suggestions = []
            if context and hasattr(context, 'suggestions'):
                for s in context.suggestions:
                    if hasattr(s, 'to_dict'):
                        suggestions.append(s.to_dict())
                    else:
                        suggestions.append(s)
                logger.info(f"Permission context has {len(suggestions)} suggestions")

            # INJECT: Add setMode suggestion for ExitPlanMode when in plan mode
            if tool_name == 'ExitPlanMode':
                try:
                    session_info = await self.coordinator.session_manager.get_session_info(session_id)
                    if session_info and session_info.current_permission_mode == 'plan':
                        # Create setMode suggestion to allow transition to acceptEdits
                        setmode_suggestion = {
                            'type': 'setMode',
                            'mode': 'acceptEdits',
                            'destination': 'session'
                        }

                        # Prepend so it appears first in UI
                        suggestions.insert(0, setmode_suggestion)
                        logger.info(f"Injected setMode suggestion for ExitPlanMode in session {session_id}")
                except Exception:
                    logger.exception("Failed to inject setMode suggestion for ExitPlanMode")

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
                    tool_use_id=tool_use_id,   # Issue #953
                    agent_id=agent_id,          # Issue #953
                )

                # Wrap in StoredMessage for triggering_message data (Issue #494: no longer stored separately)
                stored_msg = StoredMessage.from_permission_request(permission_request)
                storage_data = stored_msg.to_dict()

                # Issue #324: Update ToolCall to awaiting_permission and emit unified tool_call message
                try:
                    # Issue #953: Prefer direct O(1) lookup by tool_use_id (SDK v0.1.52+)
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

                    # Fallback: signature matching for SDK versions without tool_use_id
                    if not tool_call:
                        tool_call = self.coordinator.find_tool_call_by_signature(
                            session_id, tool_name, input_params
                        )

                        # Issue #858: Replace polling loop with asyncio.Event wait.
                        # create_tool_call() signals the event synchronously when a new
                        # ToolCall is stored, so we wait at most 5 s instead of polling.
                        if not tool_call:
                            event = self.coordinator.get_tool_call_event(session_id)
                            loop = asyncio.get_running_loop()
                            deadline = loop.time() + 5.0  # 5-second timeout
                            while True:
                                remaining = deadline - loop.time()
                                if remaining <= 0:
                                    break
                                # Clear before wait to avoid missing a signal that arrives between
                                # our find() call and the wait() call.
                                event.clear()
                                tool_call = self.coordinator.find_tool_call_by_signature(
                                    session_id, tool_name, input_params
                                )
                                if tool_call:
                                    break
                                try:
                                    await asyncio.wait_for(event.wait(), timeout=remaining)
                                except TimeoutError:
                                    break
                                # Event fired — check again
                                tool_call = self.coordinator.find_tool_call_by_signature(
                                    session_id, tool_name, input_params
                                )
                                if tool_call:
                                    break
                        if not tool_call:
                            logger.warning(
                                f"[PERMISSIONS] Race condition NOT resolved after 5.0s "
                                f"for {tool_name} in session {session_id}. "
                                f"Auto-denying permission to prevent deadlock."
                            )

                    if tool_call:
                        # Create PermissionInfo from suggestions
                        permission_info = PermissionInfo(
                            message=f"Allow {tool_name}?",
                            suggestions=[s.to_dict() for s in suggestion_objects],
                            risk_level="medium",
                        )

                        # Update tool call status
                        updated_tool_call = self.coordinator.update_tool_call_permission_request(
                            session_id,
                            tool_call.tool_use_id,
                            permission_info,
                            triggering_message=storage_data,  # Issue #494: embed permission request data
                        )

                        if updated_tool_call:
                            # Emit unified tool_call message
                            tool_call_data = updated_tool_call.to_dict()
                            tool_call_data["type"] = "tool_call"
                            tool_call_data["request_id"] = request_id  # For permission response correlation

                            websocket_message = {
                                "type": "message",
                                "session_id": session_id,
                                "data": tool_call_data,
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            if session_id in self.session_queues:
                                self.session_queues[session_id].append(websocket_message)
                            logger.info(f"Appended tool_call awaiting_permission for {tool_name} in session {session_id}")
                    else:
                        # Issue #616: No ToolCall found after retries — auto-deny to prevent deadlock
                        logger.warning(
                            f"Could not find matching ToolCall for permission request: "
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

            # Set session state to PAUSED while waiting for permission response
            # This provides visual feedback that the session is blocked on user input
            try:
                await self.coordinator.session_manager.pause_session(session_id)
                logger.info(f"Set session {session_id} to PAUSED state while waiting for permission")
            except Exception:
                logger.exception(f"Failed to pause session {session_id} for permission wait")

            # Wait for user permission decision via WebSocket
            logger.info(f"PERMISSION CALLBACK: Creating Future for request_id {request_id}")

            # Create a Future to wait for user response
            permission_future = asyncio.Future()
            self.pending_permissions[request_id] = permission_future

            try:
                # Wait for user decision (no timeout - wait indefinitely)
                logger.info(f"PERMISSION CALLBACK: Waiting for user decision on request_id {request_id}")
                response = await permission_future
                logger.info(f"PERMISSION CALLBACK: Received user decision for request_id {request_id}: {response}")

                # Restore session to ACTIVE state after permission decision
                # The session will continue processing, which will show as ACTIVE+processing (purple)
                try:
                    session_info = await self.coordinator.session_manager.get_session_info(session_id)
                    if session_info and session_info.state == SessionState.PAUSED:
                        # Use update_session_state instead of start_session to avoid re-initializing SDK
                        await self.coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)
                        logger.info(f"Restored session {session_id} to ACTIVE state after permission decision")
                except Exception:
                    logger.exception(f"Failed to restore session {session_id} to ACTIVE after permission")

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

                        # Immediately update our session state to reflect the SDK's internal mode change
                        if suggestion_dict['type'] == 'setMode' and suggestion_dict.get('mode'):
                            new_mode = suggestion_dict['mode']
                            try:
                                await self.coordinator.session_manager.update_permission_mode(session_id, new_mode)
                                logger.info(f"Updated session {session_id} permission mode to {new_mode}")
                            except Exception:
                                logger.exception("Failed to update session mode")

                        # Issue #630: Persist addDirectories to session configuration
                        if suggestion_dict['type'] == 'addDirectories' and suggestion_dict.get('directories'):
                            try:
                                await self.coordinator.session_manager.update_additional_directories(
                                    session_id, suggestion_dict['directories']
                                )
                                logger.info(
                                    f"Updated session {session_id} additional_directories "
                                    f"with {len(suggestion_dict['directories'])} dirs"
                                )
                            except Exception:
                                logger.exception("Failed to update session directories")

                    response['updated_permissions'] = updated_permissions
                    response['applied_updates_for_storage'] = applied_updates_for_storage
                    logger.info(f"Built {len(updated_permissions)} permission updates from suggestions")

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
                                            logger.info(
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
                            logger.info(
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

                # Wrap in StoredMessage for triggering_message data (Issue #494: no longer stored separately)
                stored_msg = StoredMessage.from_permission_response(permission_response_msg)
                storage_data = stored_msg.to_dict()

                # Issue #324: Update ToolCall after permission response and emit unified tool_call message
                try:
                    # Issue #953: Prefer direct lookup by tool_use_id, fall back to signature
                    tool_call = None
                    if tool_use_id:
                        tool_call = self.coordinator.get_tool_call_by_id(session_id, tool_use_id)
                    if not tool_call:
                        tool_call = self.coordinator.find_tool_call_by_signature(
                            session_id, tool_name, input_params
                        )

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
                            triggering_message=storage_data,  # Issue #494: embed permission response data
                            applied_updates=applied_updates_dicts,
                        )

                        if updated_tool_call:
                            # Emit unified tool_call message
                            tool_call_data = updated_tool_call.to_dict()
                            tool_call_data["type"] = "tool_call"

                            websocket_message = {
                                "type": "message",
                                "session_id": session_id,
                                "data": tool_call_data,
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            if session_id in self.session_queues:
                                self.session_queues[session_id].append(websocket_message)
                            logger.info(
                                f"Appended tool_call {'running' if granted else 'denied'} "
                                f"for {tool_name} in session {session_id}"
                            )
                except Exception:
                    logger.exception("Failed to update ToolCall for permission response")

                # Issue #491: Legacy permission_response broadcast removed.
                # Only unified tool_call messages are emitted for permission lifecycle.

            except Exception:
                logger.exception("Failed to store permission response message")

            logger.info(f"Permission {decision} for tool: {tool_name} (request_id: {request_id})")
            return response

        return permission_callback

    def cleanup_pending_for_session(self, session_id: str) -> None:
        """Clean up pending permissions for a specific session by auto-denying them"""
        try:
            # Find all pending permissions for this session and auto-deny them
            permissions_to_cleanup = []
            for request_id, future in self.pending_permissions.items():
                if not future.done():
                    # We need to identify which permissions belong to this session
                    # Since the permission callback stores the session_id in its closure,
                    # we'll auto-deny all pending permissions when a session terminates
                    # This is safe because if a session is terminated, no tools should execute
                    permissions_to_cleanup.append(request_id)

            for request_id in permissions_to_cleanup:
                future = self.pending_permissions.pop(request_id, None)
                if future and not future.done():
                    # Auto-deny the permission
                    response = {
                        "behavior": "deny",
                        "message": f"Session {session_id} terminated - auto-denying pending permission"
                    }
                    future.set_result(response)
                    logger.info(f"Auto-denied pending permission {request_id} due to session {session_id} termination")

            if permissions_to_cleanup:
                logger.info(f"Cleaned up {len(permissions_to_cleanup)} pending permissions for session {session_id}")

        except Exception:
            logger.exception(f"Error cleaning up pending permissions for session {session_id}")

    def deny_all_for_interrupt(self) -> None:
        """Deny all pending permission requests due to a session interrupt."""
        for request_id, future in list(self.pending_permissions.items()):
            if not future.done():
                future.set_result({
                    "behavior": "deny",
                    "message": "Permission denied due to session interrupt",
                    "interrupt": True
                })
                self.pending_permissions.pop(request_id, None)

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
