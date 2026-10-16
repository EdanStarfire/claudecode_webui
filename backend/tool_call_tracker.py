"""
Tool Call Tracker for Claude Code WebUI

Extracted from SessionCoordinator (issue #2189, epic #1990 stage 7
`coordinator-split`, PR 3): owns the ToolCall lifecycle tracking cluster
(creation, permission request/response, running/result/input updates,
interruption, snapshot application). This is a mechanical extraction —
SessionCoordinator keeps every public method as a one-line delegate, and
every per-session shared dict stays constructed on the coordinator;
ToolCallTracker receives the same dict objects by reference, never copies.

Unlike SessionLauncher/MessagePipeline, this is the terminal content
extraction of the coordinator-split stage: nothing it calls is still
stranded on the coordinator, so no Callbacks dataclass is needed here.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from shared.logging_config import get_logger

from .models.messages import MessageRecord, PermissionInfo, ToolCall, ToolDisplayInfo, ToolState
from .task_utils import task_done_log_exception

# Get specialized logger for coordinator actions (same category as the
# coordinator's own coord_logger — this is still "coordinator actions" from
# an operator's perspective, just physically relocated).
coord_logger = get_logger('coordinator', category='COORDINATOR')
# Issue #2189 (PR 3): preserved, not fixed — mark_session_tools_interrupted's
# broadcast-exception handler has always used the plain `logger` here while
# the rest of this cluster uses `coord_logger`.
logger = logging.getLogger(__name__)


class ToolCallTracker:
    """Owns ToolCall lifecycle tracking: creation, permission request/response,
    running/result/input updates, interruption, and snapshot application.
    """

    def __init__(self, *, coordinator: Any):
        # Issue #2189: every shared per-session dict is a LIVE property lookup
        # against the coordinator (below), not a value snapshotted here at
        # construction time — same discipline as SessionLauncher/MessagePipeline
        # (PR 1/2). A snapshot would go stale the moment any of them is
        # reassigned on the coordinator after construction.
        self._coordinator = coordinator

    # --- Shared per-session state: live passthrough to the coordinator -------
    _storage_managers = property(lambda self: self._coordinator._storage_managers)
    _active_tool_calls = property(lambda self: self._coordinator._active_tool_calls)
    _tool_call_events = property(lambda self: self._coordinator._tool_call_events)
    _tool_call_broadcast_callbacks = property(
        lambda self: self._coordinator._tool_call_broadcast_callbacks
    )
    _watchdog = property(lambda self: self._coordinator._watchdog)
    session_manager = property(lambda self: self._coordinator.session_manager)
    get_or_create_storage_manager = property(lambda self: self._coordinator.get_or_create_storage_manager)

    # ============================================================
    # Issue #494: ToolCallUpdate Storage
    # ============================================================

    def _schedule_tool_call_update_storage(
        self,
        session_id: str,
        tool_call: ToolCall,
        triggering_message: dict[str, Any] | None = None,
    ) -> None:
        """
        Snapshot and schedule storage of a ToolCallUpdate entry (Issue #494).

        The MessageRecord is built eagerly (synchronously) to capture the
        current tool_call state before the object is mutated by subsequent
        lifecycle transitions.  The actual I/O is deferred via ensure_future.
        """
        storage = self._storage_managers.get(session_id)
        if not storage:
            coord_logger.warning(
                f"No storage manager for session {session_id}, skipping ToolCallUpdate"
            )
            return
        try:
            stored_dict = MessageRecord.from_tool_call(
                tool_call, triggering_message
            ).to_dict()
        except Exception:
            coord_logger.exception(
                f"Failed to build ToolCallUpdate for {tool_call.tool_use_id}"
            )
            return
        task = asyncio.ensure_future(
            self._write_tool_call_update(storage, stored_dict, tool_call.tool_use_id)
        )
        task.add_done_callback(task_done_log_exception)

    async def _write_tool_call_update(
        self,
        storage,
        stored_dict: dict[str, Any],
        tool_use_id: str,
    ) -> None:
        """Write a pre-built ToolCallUpdate dict to storage (Issue #494)."""
        try:
            await storage.append_message(stored_dict)
        except Exception:
            coord_logger.exception(
                f"Failed to store ToolCallUpdate for {tool_use_id}"
            )

    # ============================================================
    # Issue #707: Auto-Approval Callback
    # ============================================================

    def _create_auto_approval_callback(self, session_id: str) -> Callable:
        """
        Create a callback that can_use_tool calls when it auto-approves a tool.

        The ToolCall already exists (created from the AssistantMessage tool_use block
        before can_use_tool fires), so we find it by tool_use_id via the existing O(1)
        get_tool_call_by_id() lookup, set the reason, store a new ToolCallUpdate, and
        broadcast via the existing tool_call broadcast mechanism.
        """
        def callback(
            tool_name: str, input_params: dict[str, Any], reason: str, tool_use_id: str | None
        ) -> None:
            # Auto-approval fires before any permission request exists (it short-circuits
            # _can_use_tool_callback before the branch that mints a request_id), so there is
            # no request_id to fall back to here — tool_use_id is the only linkage (#2126).
            tool_call = self.get_tool_call_by_id(session_id, tool_use_id) if tool_use_id else None
            if (
                tool_call is None
                or tool_call.auto_approved_reason is not None
                or tool_call.status != ToolState.PENDING
            ):
                return
            tool_call.auto_approved_reason = reason
            coord_logger.info(
                f"[707] Auto-approved {tool_call.tool_use_id} ({tool_name}): {reason}"
            )
            # Store a new ToolCallUpdate with the reason attached
            self._schedule_tool_call_update_storage(session_id, tool_call)
            # Broadcast via existing tool_call broadcast mechanism
            tool_call_data = MessageRecord.from_tool_call(tool_call).to_dict()
            for cb in self._tool_call_broadcast_callbacks:
                try:
                    cb(session_id, tool_call_data)
                except Exception:
                    coord_logger.exception("Error in auto-approval broadcast callback")
        return callback

    # ============================================================
    # Issue #324: Unified ToolCall Lifecycle Management
    # ============================================================

    def create_tool_call(
        self,
        session_id: str,
        tool_use_id: str,
        name: str,
        input_params: dict[str, Any],
        requires_permission: bool = False,
        parent_tool_use_id: str | None = None,
        turn_id: str | None = None,
    ) -> ToolCall:
        """
        Create a new ToolCall when tool_use is detected (Issue #324).

        Called when an AssistantMessage contains a tool_use block.
        Returns the created ToolCall for WebSocket broadcast.
        """
        tool_call = ToolCall(
            tool_use_id=tool_use_id,
            session_id=session_id,
            name=name,
            input=input_params,
            status=ToolState.PENDING,
            created_at=time.time(),
            requires_permission=requires_permission,
            parent_tool_use_id=parent_tool_use_id,
            turn_id=turn_id,
            display=ToolDisplayInfo(
                state=ToolState.PENDING,
                visible=True,
                collapsed=False,
                style="default",
            ),
        )

        # Track in active tool calls
        if session_id not in self._active_tool_calls:
            self._active_tool_calls[session_id] = {}
        self._active_tool_calls[session_id][tool_use_id] = tool_call

        # Issue #858: Notify any permission callbacks waiting for this tool call.
        if session_id in self._tool_call_events:
            self._tool_call_events[session_id].set()

        coord_logger.debug(
            f"Created ToolCall {tool_use_id} for {name} in session {session_id}"
        )

        # Issue #494: Store PENDING ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call)

        return tool_call

    def update_tool_call_permission_request(
        self,
        session_id: str,
        tool_use_id: str,
        permission_info: PermissionInfo,
        request_id: str | None = None,
        triggering_message: dict[str, Any] | None = None,
    ) -> ToolCall | None:
        """
        Update ToolCall to awaiting_permission status (Issue #324).

        Called when a permission request is received for a tool.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for permission request in session {session_id}"
            )
            return None

        tool_call.status = ToolState.AWAITING_PERMISSION
        tool_call.requires_permission = True
        tool_call.permission = permission_info
        tool_call.request_id = request_id
        if tool_call.display:
            tool_call.display.state = ToolState.AWAITING_PERMISSION
            tool_call.display.style = "warning"

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to awaiting_permission in session {session_id}"
        )

        # Issue #494: Store AWAITING_PERMISSION ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call, triggering_message)

        return tool_call

    def update_tool_call_permission_response(
        self,
        session_id: str,
        tool_use_id: str,
        granted: bool,
        triggering_message: dict[str, Any] | None = None,
        applied_updates: list[dict[str, Any]] | None = None,
    ) -> ToolCall | None:
        """
        Update ToolCall after permission response (Issue #324).

        Called when user grants or denies permission.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for permission response in session {session_id}"
            )
            return None

        tool_call.permission_granted = granted
        tool_call.permission_response_at = time.time()
        if applied_updates:
            tool_call.applied_updates = applied_updates

        if granted:
            tool_call.status = ToolState.RUNNING
            tool_call.started_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.RUNNING
                tool_call.display.style = "default"
        else:
            tool_call.status = ToolState.DENIED
            tool_call.completed_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.DENIED
                tool_call.display.style = "error"
            # Remove from active (denied tools are terminal)
            self._remove_active_tool_call(session_id, tool_use_id)

            # Issue #1131: Record DENIED outcome for error-rate watchdog
            if self._watchdog is not None:
                self._watchdog.record_tool_outcome(session_id, tool_use_id, "denied")

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} permission_granted={granted} in session {session_id}"
        )

        # Issue #494: Store RUNNING or DENIED ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call, triggering_message)

        return tool_call

    def update_tool_call_running(
        self,
        session_id: str,
        tool_use_id: str,
    ) -> ToolCall | None:
        """
        Update ToolCall to running status (Issue #324).

        Called when a tool starts executing (for tools that don't require permission).
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            # Tool may not exist yet if tool_use and execution happen quickly
            coord_logger.debug(
                f"ToolCall {tool_use_id} not found for running update in session {session_id}"
            )
            return None

        tool_call.status = ToolState.RUNNING
        tool_call.started_at = time.time()
        if tool_call.display:
            tool_call.display.state = ToolState.RUNNING
            tool_call.display.style = "default"

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to running in session {session_id}"
        )

        # Issue #494: Store RUNNING ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call)

        return tool_call

    def update_tool_call_result(
        self,
        session_id: str,
        tool_use_id: str,
        result: Any,
        is_error: bool = False,
        triggering_message: dict[str, Any] | None = None,
        sender_attachments: list[dict] | None = None,
    ) -> ToolCall | None:
        """
        Update ToolCall with result (Issue #324).

        Called when a tool_result is received.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for result in session {session_id}"
            )
            return None

        tool_call.completed_at = time.time()
        tool_call.result = result

        if is_error:
            tool_call.status = ToolState.FAILED
            tool_call.error = str(result) if result else "Tool execution failed"
            if tool_call.display:
                tool_call.display.state = ToolState.FAILED
                tool_call.display.style = "error"
        else:
            tool_call.status = ToolState.COMPLETED
            if tool_call.display:
                tool_call.display.state = ToolState.COMPLETED
                tool_call.display.style = "success"

        # Issue #1593: Attach sender attachment resource IDs for send_comm chips
        if sender_attachments is not None:
            tool_call.sender_attachments = sender_attachments

        # Remove from active (completed/failed tools are terminal)
        self._remove_active_tool_call(session_id, tool_use_id)

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to {'failed' if is_error else 'completed'} in session {session_id}"
        )

        # Issue #494: Store COMPLETED or FAILED ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call, triggering_message)

        # Issue #1131: Record terminal tool outcome for error-rate watchdog
        if self._watchdog is not None:
            outcome = "failed" if is_error else "completed"
            self._watchdog.record_tool_outcome(session_id, tool_use_id, outcome)

        return tool_call

    def update_tool_call_input(
        self,
        session_id: str,
        tool_use_id: str,
        input_params: dict[str, Any],
    ) -> ToolCall | None:
        """
        Fill in the real input on a ToolCall created early, at content_block_start
        (Issue #2109, AC2). Returns the updated ToolCall, or None if no pending
        record exists yet for this tool_use_id (the non-streaming/fallback caller
        should create one fresh instead).
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            return None

        tool_call.input = input_params

        # Issue #858: the legacy signature-matching permission fallback hashes
        # tool_call.input — it just changed from {} to the real input, so any
        # waiter blocked on this event needs to re-check now, exactly like
        # create_tool_call() already does for a brand-new ToolCall.
        if session_id in self._tool_call_events:
            self._tool_call_events[session_id].set()

        coord_logger.debug(
            f"Filled in input for ToolCall {tool_use_id} in session {session_id}"
        )

        self._schedule_tool_call_update_storage(session_id, tool_call)

        return tool_call

    def mark_session_tools_interrupted(self, session_id: str) -> list[ToolCall]:
        """
        Mark all active tools for a session as interrupted (Issue #324).

        Called when session is terminated or interrupted.
        Stores INTERRUPTED ToolCallUpdate entries and broadcasts via WebSocket.
        Returns list of interrupted ToolCalls.
        """
        interrupted = []
        session_tools = self._active_tool_calls.get(session_id, {})

        for _tool_use_id, tool_call in list(session_tools.items()):
            tool_call.status = ToolState.INTERRUPTED
            tool_call.completed_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.INTERRUPTED
                tool_call.display.style = "orphaned"
            interrupted.append(tool_call)

            # Issue #494: Store INTERRUPTED ToolCallUpdate for each tool
            self._schedule_tool_call_update_storage(session_id, tool_call)

        # Clear active tools for session
        if session_id in self._active_tool_calls:
            self._active_tool_calls[session_id].clear()

        if interrupted:
            coord_logger.info(
                f"Marked {len(interrupted)} tools as interrupted for session {session_id}"
            )
            # Issue #520: Broadcast interrupted tool_call messages via WebSocket
            for tool_call in interrupted:
                tool_call_data = MessageRecord.from_tool_call(tool_call).to_dict()
                for cb in self._tool_call_broadcast_callbacks:
                    try:
                        cb(session_id, tool_call_data)
                    except Exception:
                        logger.exception("Error in tool_call broadcast callback")

        return interrupted

    def get_tool_call_event(self, session_id: str) -> asyncio.Event:
        """Return (creating if necessary) the per-session tool-call notification event."""
        if session_id not in self._tool_call_events:
            self._tool_call_events[session_id] = asyncio.Event()
        return self._tool_call_events[session_id]

    def get_tool_call_by_id(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        """O(1) lookup of an active tool call by its tool_use_id (Issue #953)."""
        return self._active_tool_calls.get(session_id, {}).get(tool_use_id)

    def _get_active_tool_call(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        """Get a specific active tool call."""
        session_tools = self._active_tool_calls.get(session_id, {})
        return session_tools.get(tool_use_id)

    def apply_tool_call_snapshot(self, session_id: str, tool_call: ToolCall) -> ToolCall:
        """Issue #2109 (AC11): replay-only — installs an already-complete ToolCall
        snapshot (e.g. a regenerated fixture's reconstructed pending/awaiting_permission/
        running|denied record) as the session's tracked state for that tool_use_id
        verbatim, rather than deriving a transition from partial fields. Used by
        BackendApp._emit_tool_call_updates()'s flat `tool_call` replay-mirror branch.

        A terminal snapshot (denied/completed/failed/interrupted) is removed from
        active tracking and recorded on the watchdog — mirroring every other
        terminal-state transition method (update_tool_call_permission_response's
        deny path, update_tool_call_result's completed/failed path) — so it can't
        linger in _active_tool_calls and later get silently clobbered back to
        INTERRUPTED by a mark_session_tools_interrupted() bulk sweep.
        """
        if session_id not in self._active_tool_calls:
            self._active_tool_calls[session_id] = {}
        self._active_tool_calls[session_id][tool_call.tool_use_id] = tool_call

        if tool_call.status in (
            ToolState.DENIED, ToolState.COMPLETED, ToolState.FAILED, ToolState.INTERRUPTED,
        ):
            self._remove_active_tool_call(session_id, tool_call.tool_use_id)
            if tool_call.status != ToolState.INTERRUPTED and self._watchdog is not None:
                self._watchdog.record_tool_outcome(session_id, tool_call.tool_use_id, tool_call.status.value)

        return tool_call

    def _remove_active_tool_call(self, session_id: str, tool_use_id: str) -> None:
        """Remove a tool call from active tracking."""
        session_tools = self._active_tool_calls.get(session_id, {})
        if tool_use_id in session_tools:
            del session_tools[tool_use_id]

    def get_tool_call_by_request_id(self, session_id: str, request_id: str) -> ToolCall | None:
        """Lookup of an active tool call awaiting permission by its request_id
        (Issue #2109, AC6). Scoped to AWAITING_PERMISSION so a duplicate/replayed
        permission_response sharing a stale request_id can't re-resolve a tool call
        that already moved on to running/denied — request_id is never cleared off
        the ToolCall once set, so an unscoped match would still find it."""
        for tool_call in self._active_tool_calls.get(session_id, {}).values():
            if tool_call.request_id == request_id and tool_call.status == ToolState.AWAITING_PERMISSION:
                return tool_call
        return None

    async def _repair_crash_orphaned_tool_calls(self) -> None:
        """Issue #2111: repair tool calls left non-terminal across a genuine Backend
        process crash.

        mark_session_tools_interrupted() is the only code that ever writes a terminal
        INTERRUPTED tool_call record, and it operates purely on the in-memory
        _active_tool_calls dict — populated only while a live Backend process is
        running. If the process crashes outright (not a graceful terminate_session()/
        interrupt_session()/restart_session()/critical-error callback), none of those
        call sites ever runs, and the open tool's last on-disk record stays
        pending/awaiting_permission/running forever.

        Scoped to exactly the sessions self.session_manager flags as needing a repair
        scan this boot (the only ones that could plausibly have had an open tool call)
        so this is a no-op once a session has settled into TERMINATED/CREATED on a
        prior boot. Mirrors mark_session_tools_interrupted()'s mutation exactly, but
        writes directly via get_or_create_storage_manager() + append_message() since
        _active_tool_calls is empty at boot and there's no broadcast subscriber yet to
        notify.

        Issue #2127: this repair write lands before BackendApp wires its AuditWriter
        (set_audit_writer() runs after coordinator.initialize() returns), so there's
        no `on_append` hook attached yet to capture the synthetic repaired record the
        way every live mark_session_tools_interrupted() write is captured. Instead,
        each repaired record is queued into `self._coordinator._crash_repair_audit_backlog`
        alongside the storage write; `set_audit_writer()` drains that backlog and
        replays the records into the real AuditWriter once it exists. Each session is
        repaired independently and failures are isolated (see except below) so a
        single bad session can't block the others or abort Backend startup, but
        nothing currently retries a session whose repair itself fails this boot;
        that remains an accepted scope boundary for this issue, not addressed here.
        """
        non_terminal_statuses = {
            ToolState.PENDING.value,
            ToolState.AWAITING_PERMISSION.value,
            ToolState.RUNNING.value,
        }

        for session_id in self.session_manager.get_sessions_needing_tool_repair_scan():
            try:
                storage = await self.get_or_create_storage_manager(session_id)
                if storage is None:
                    continue

                # Cumulative snapshots: keep only the last-occurring record per
                # tool_use_id (append order, not timestamp value).
                latest_by_tool_use_id: dict[str, dict[str, Any]] = {}
                for record in await storage.read_messages():
                    if record.get("type") != "tool_call":
                        continue
                    tool_use_id = record.get("tool_use_id")
                    if tool_use_id:
                        latest_by_tool_use_id[tool_use_id] = record

                for latest_record in latest_by_tool_use_id.values():
                    if latest_record.get("status") not in non_terminal_statuses:
                        continue

                    tool_call = ToolCall.from_dict(latest_record)
                    tool_call.status = ToolState.INTERRUPTED
                    tool_call.completed_at = time.time()
                    tool_call.repair_reason = "boot_crash_repair"
                    if tool_call.display:
                        tool_call.display.state = ToolState.INTERRUPTED
                        tool_call.display.style = "orphaned"

                    record = MessageRecord.from_tool_call(tool_call).to_dict()
                    await storage.append_message(record)
                    # Deliberately not a property: single write site, no read side.
                    self._coordinator._crash_repair_audit_backlog.append((session_id, record))
                    coord_logger.info(
                        f"Issue #2111: repaired crash-orphaned tool call "
                        f"{tool_call.tool_use_id} for session {session_id} on boot"
                    )
            except Exception:
                # Don't raise - one session's repair failure shouldn't block
                # coordinator initialization or the repair of other sessions.
                coord_logger.exception(
                    f"Issue #2111: failed to repair crash-orphaned tool calls for "
                    f"session {session_id}"
                )
