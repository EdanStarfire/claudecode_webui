"""
CommRouter - Communication routing for Legion multi-agent system.

Responsibilities:
- Convert Comm → Message for SDK injection
- Convert SDK Message → Comm for routing
- Route Comms to minions or user (direct communication only)
- Handle interrupt priorities (Halt, Pivot)
- Broadcast persisted Comms to WebSocket/UI notification/audit writer
- Parse #minion-name tags for explicit references
"""

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from backend.models.legion_models import (
    SYSTEM_MINION_ID,
    SYSTEM_MINION_NAME,
    Comm,
    CommType,
    DeliveryPolicy,
)
from backend.session_manager import SessionState
from shared.logging_config import get_logger

if TYPE_CHECKING:
    from backend.legion_system import LegionSystem

# Legion logger
legion_logger = get_logger('legion', 'COMM_ROUTER')


class CommRouter:
    """Routes direct communications between minions and user."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize CommRouter with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system
        self._comm_broadcast_callback = None  # Callback for broadcasting new comms via WebSocket
        self._ui_notification_callback = None  # Callback for UI notification events (Issue #699)
        self.audit_writer = None  # Optional AuditWriter for comm audit capture (#1127)

    async def get_visible_minions(self, caller_id: str) -> list[str]:
        """
        Return minion IDs visible to the caller based on full hierarchy chain.

        A minion can see:
        1. All ancestors (parent, grandparent, etc.)
        2. All descendants (children, grandchildren, etc.)
        3. Direct siblings (other children of same parent)

        The user always sees all minions (handled separately).

        Args:
            caller_id: Session ID of the calling minion

        Returns:
            List of visible minion session IDs
        """
        session_manager = self.system.session_port.session_manager
        caller = await session_manager.get_session_info(caller_id)
        if not caller:
            return []

        visible = set()
        visible.add(caller_id)

        async def add_ancestors(sid: str, visited: set):
            if sid in visited:
                return
            visited.add(sid)
            s = await session_manager.get_session_info(sid)
            if s and s.parent_overseer_id:
                visible.add(s.parent_overseer_id)
                await add_ancestors(s.parent_overseer_id, visited)

        async def add_descendants(sid: str, visited: set):
            if sid in visited:
                return
            visited.add(sid)
            s = await session_manager.get_session_info(sid)
            if s and s.child_minion_ids:
                for child_id in s.child_minion_ids:
                    visible.add(child_id)
                    await add_descendants(child_id, visited)

        await add_ancestors(caller_id, set())
        await add_descendants(caller_id, set())

        # Siblings (children of direct parent)
        if caller.parent_overseer_id:
            parent = await session_manager.get_session_info(caller.parent_overseer_id)
            if parent and parent.child_minion_ids:
                visible.update(parent.child_minion_ids)

        return list(visible)

    async def validate_comm_target(self, sender_id: str, recipient_id: str) -> bool:
        """
        Check if sender can directly communicate with recipient.

        User (sender_id=None or "user") can always reach anyone.
        Minions can only reach their full hierarchy chain (ancestors, descendants, siblings).

        Args:
            sender_id: Session ID of sender (None for user)
            recipient_id: Session ID of recipient

        Returns:
            True if sender can comm recipient
        """
        if not sender_id or sender_id == "user":
            return True
        return recipient_id in await self.get_visible_minions(sender_id)

    async def is_ancestor(self, candidate_id: str, descendant_id: str) -> bool:
        """True if candidate_id is an ancestor (parent, grandparent, ...) of descendant_id.
        Generalizes get_visible_minions()'s add_ancestors() walk into a direct membership
        check (issue #2193 AC7), rather than full-hierarchy visibility."""
        session_manager = self.system.session_port.session_manager
        visited: set[str] = set()
        current_id = descendant_id
        while True:
            session = await session_manager.get_session_info(current_id)
            if not session or not session.parent_overseer_id:
                return False
            if session.parent_overseer_id == candidate_id:
                return True
            if session.parent_overseer_id in visited:
                return False  # cycle guard
            visited.add(session.parent_overseer_id)
            current_id = session.parent_overseer_id

    async def is_authorized_sender(self, comm: Comm) -> bool:
        """True if comm's sender may HALT/PIVOT comm.to_minion_id, or implicitly resume a
        paused session (issue #2193 AC6/AC7): the user, or an ancestor of the recipient."""
        if comm.from_user:
            return True
        if not comm.from_minion_id:
            return False
        return await self.is_ancestor(comm.from_minion_id, comm.to_minion_id)

    def set_comm_broadcast_callback(self, callback):
        """
        Set callback for broadcasting new comms to WebSocket clients.

        Args:
            callback: Async function(legion_id, comm) to broadcast comm events
        """
        self._comm_broadcast_callback = callback

    def set_ui_notification_callback(self, callback):
        """
        Set callback for pushing notification events to the UI WebSocket.

        Issue #699: Decouples comm notifications from Legion WebSocket,
        ensuring sounds fire independent of whether any specific UI panel is open.

        Args:
            callback: Async function(comm) to broadcast UI notification
        """
        self._ui_notification_callback = callback

    async def route_comm(
        self,
        comm: Comm,
        attachment_data: dict[str, bytes] | None = None,
    ) -> bool:
        """
        Route a Comm to its destination(s).

        If legion is in emergency halt, queues comm instead of routing.

        Args:
            comm: Comm object to route
            attachment_data: Optional dict mapping filename -> bytes for file attachments

        Returns:
            bool: True if routing succeeded

        Raises:
            ValueError: If comm validation fails
        """
        legion_logger.debug(f"Routing comm {comm.comm_id}: from={comm.from_minion_id}, to_minion={comm.to_minion_id}, to_user={comm.to_user}")

        # Validate comm has proper routing
        comm.validate()

        # Issue #2228: reject an unauthorized sibling's HALT/PIVOT before any
        # side effect fires. DeliveryGate._is_authorized() (backend/delivery_gate.py)
        # already blocks the actual interrupt/pivot action, but only runs inside
        # deliver(), itself only reached after _deliver_attachments()/_persist_comm()
        # have already fired unconditionally. is_authorized_sender() is the exact
        # helper _is_authorized() delegates to for minion senders — reused here as
        # a second, earlier call site for side-effect prevention, not duplicated
        # authority logic. HALT/PIVOT-to-user (to_minion_id=None) is never gated
        # here, matching deliver()'s own scoping (it's never reached for to_user).
        #
        # Gated on the target session actually existing: is_ancestor()'s hierarchy
        # walk starts from comm.to_minion_id's own session info and returns False
        # if that session is missing — indistinguishable from "no ancestor
        # relationship" from is_authorized_sender()'s own return value alone. In
        # production, deliver() never confuses the two because it checks the
        # session directory (session_dir) before ever reaching its own authority
        # check. Checking existence here first preserves that same ordering: a
        # missing target falls through to the existing, already-tested path
        # (_deliver_attachments()'s own missing-target skip, then deliver()'s
        # "Session directory not found") instead of being misreported as an
        # authority failure.
        if comm.delivery in (DeliveryPolicy.HALT, DeliveryPolicy.PIVOT) and comm.to_minion_id:
            target_exists = await self.system.session_port.session_manager.get_session_info(
                comm.to_minion_id
            )
            if target_exists and not await self.is_authorized_sender(comm):
                if comm.delivery == DeliveryPolicy.HALT:
                    reason = "HALT requires the sender to be the user or an ancestor of the recipient"
                else:
                    reason = "PIVOT requires the sender to be the user or an ancestor of the recipient"
                comm.metadata["delivery_failure_reason"] = reason
                legion_logger.warning(
                    f"Rejected unauthorized {comm.delivery.value.upper()} from {comm.from_minion_id} "
                    f"to {comm.to_minion_id}: {reason}"
                )
                return False

        # Deliver attachments before persisting (issue #1730) so _persist_comm()'s
        # consumers (e.g. the audit writer's comm_dict) capture the resolved
        # resource_id from the start, instead of null placeholders mutated in
        # place afterward.
        if comm.to_minion_id:
            await self._deliver_attachments(comm, attachment_data)

        # Normal routing - persist and route
        await self._persist_comm(comm)
        legion_logger.debug(f"Comm {comm.comm_id} persisted successfully")

        # Route to destination
        if comm.to_minion_id:
            result = await self._send_to_minion(comm)
            legion_logger.info(f"Comm {comm.comm_id} routed to minion {comm.to_minion_id}: {'success' if result else 'failed'}")
            return result
        elif comm.to_user:
            result = await self._send_to_user(comm)
            legion_logger.info(f"Comm {comm.comm_id} sent to user: {'success' if result else 'failed'}")
            return result

        legion_logger.warning(f"Comm {comm.comm_id} has no valid destination")
        return False

    async def _deliver_attachments(
        self,
        comm: Comm,
        attachment_data: dict[str, bytes] | None = None,
    ) -> None:
        """
        Write and register a Comm's file attachments in the recipient's session.

        Called from route_comm() before _persist_comm() (issue #1730) so that
        _persist_comm()'s consumers capture each attachment's resolved
        resource_id from the start, instead of a null placeholder mutated in
        place afterward.

        Files go to the session's attachments/ dir — same location as user
        uploads via InputArea. To the agent, a comm is just an enhanced user
        message from another agent, so the file paths should be consistent.
        For Docker sessions, attachments/ is mounted read-only at its host
        path (see session_coordinator.py).

        Mutates each dict in comm.attachments in place with resource_id,
        session_id, and stored_path on success. On failure to locate or
        deliver a given attachment, it's left with resource_id=None — no
        worse than before this fix — and logged.
        """
        if not (comm.attachments and comm.to_minion_id):
            return

        # Skip delivery if the target minion doesn't exist — mirrors the check
        # _send_to_minion() does before it would otherwise have reached the
        # (now-removed) delivery block. Without this, a comm to a missing/
        # disposed minion would write files and register orphaned resources
        # under a session directory that isn't wired into session_manager,
        # before _send_to_minion() catches the missing target and errors out.
        target_minion = await self.system.session_port.session_manager.get_session_info(comm.to_minion_id)
        if not target_minion:
            legion_logger.warning(f"Skipping attachment delivery: target minion {comm.to_minion_id} not found")
            return

        attachment_data = attachment_data or {}
        data_dir = self.system.session_port.data_dir
        attachments_dir = data_dir / "sessions" / comm.to_minion_id / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        # Sender name for the registered resource's description only
        from_name = "Minion #user"
        if comm.from_minion_id:
            from_minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
            if from_minion and from_minion.name:
                from_name = f"Minion #{from_minion.name}"

        for att in comm.attachments:
            file_bytes = attachment_data.get(att["name"])
            if not file_bytes:
                # Fallback: try reading from source path
                source = Path(att.get("source_path", ""))
                if source.exists():
                    file_bytes = source.read_bytes()
            if not file_bytes:
                legion_logger.error(f"Failed to deliver attachment {att['name']}: no data or source found")
                continue

            try:
                dest_path = attachments_dir / att["name"]
                # Avoid name collisions
                if dest_path.exists():
                    dest_path = attachments_dir / f"{dest_path.stem}_{uuid.uuid4().hex[:8]}{dest_path.suffix}"
                dest_path.write_bytes(file_bytes)

                # Register as resource in recipient session (for UI gallery)
                resource_result = await self.system.session_port.register_uploaded_resource(
                    session_id=comm.to_minion_id,
                    file_path=str(dest_path),
                    title=att["name"],
                    description=f"File attachment from {from_name}"
                )

                # Register for auto-approve Read
                await self.system.session_port.register_uploaded_file(
                    session_id=comm.to_minion_id,
                    file_path=str(dest_path)
                )

                # Update attachment metadata with resource info
                if resource_result:
                    att["resource_id"] = resource_result.get("resource_id")
                    att["session_id"] = comm.to_minion_id
                    att["stored_path"] = str(dest_path)
            except Exception as e:
                legion_logger.error(f"Failed to deliver attachment {att['name']}: {e}")

    async def _send_to_minion(
        self,
        comm: Comm,
    ) -> bool:
        """
        Send Comm to a specific minion by injecting message into SDK session.
        Auto-starts the minion session if it's not active.

        Attachments (if any) must already be delivered via _deliver_attachments()
        before this is called — this only formats comm.attachments for display.

        Issue #2193 PR5: this is now a thin wrapper — all gating (auto-start/
        readiness-wait, AC5/AC6/AC7/AC8 ordering, HALT/PIVOT interrupt handling)
        lives in delivery_gate.py's deliver(), the single call below. This
        method keeps only the formatting logic (header, attachment listing,
        trailing instruction, comm_metadata) and the failure-notification call,
        unchanged from before this PR.

        Args:
            comm: Comm with to_minion_id set

        Returns:
            bool: True if sent successfully
        """
        try:
            # Get sender name for formatting
            # Use "Minion #user" to signal that replies should use send_comm MCP tool
            from_name = "Minion #user"

            if comm.from_minion_id:
                from_minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
                if from_minion and from_minion.name:
                    from_name = f"Minion #{from_minion.name}"

            # Format message for recipient minion
            comm_type_prefix = {
                CommType.TASK: "📋 Task",
                CommType.QUESTION: "❓ Question",
                CommType.REPORT: "📊 Report",
                CommType.INFO: "💡 Info"
            }.get(comm.comm_type, "💬 Message")

            # Use summary in header if available, otherwise use truncated content
            header_summary = comm.summary if comm.summary else (comm.content[:50] + "..." if len(comm.content) > 50 else comm.content)

            formatted_message = f"**{comm_type_prefix} from {from_name}:** {header_summary}\n\n{comm.content}"

            # Format file attachments for the recipient message (issue #773).
            # Delivery (file write + resource registration) already happened in
            # route_comm() via _deliver_attachments() (issue #1730), before
            # _persist_comm() ran, so this only builds display lines from the
            # already-resolved comm.attachments.
            if comm.attachments and comm.to_minion_id:
                attachment_lines = []
                for att in comm.attachments:
                    if att.get("stored_path"):
                        size_kb = att["size"] / 1024
                        if size_kb >= 1024:
                            size_str = f"{size_kb / 1024:.1f} MB"
                        else:
                            size_str = f"{size_kb:.1f} KB"
                        attachment_lines.append(
                            f"- {att['name']} ({size_str}): {att['stored_path']}"
                        )
                    else:
                        attachment_lines.append(f"- {att['name']}: [delivery failed]")

                if attachment_lines:
                    formatted_message += "\n\n---\nAttached files (use Read tool to access, or embed via markdown URL):\n"
                    formatted_message += "\n".join(attachment_lines)

            trailing_instruction = f"Always send messages to {from_name} using the `send_comm` tool."
            formatted_message += f"\n\n---\n{trailing_instruction}"

            # Build comm metadata for frontend styling
            from_name_slug = self._slugify(from_name.replace("Minion #", ""))
            from_display = from_name.replace("Minion #", "")
            comm_metadata = {
                "comm": {
                    "from_name": from_name_slug,
                    "from_display_name": from_display,
                    "from_minion_id": comm.from_minion_id,
                    "comm_type": comm.comm_type.value if hasattr(comm.comm_type, 'value') else str(comm.comm_type),
                    # Issue #1843: structured fields for the collapsible CommCard UI,
                    # exposed alongside (not instead of) formatted_message so the
                    # frontend never needs to reconstruct or re-derive delivered text.
                    "summary": comm.summary or "",
                    "content": comm.content or "",
                    "trailing_instruction": trailing_instruction,
                }
            }

            # Add processed attachment metadata for frontend chip rendering (issue #939)
            delivered_attachments = [a for a in comm.attachments if a.get("stored_path")]
            if delivered_attachments:
                comm_metadata["attachments"] = [
                    {
                        "filename": a["name"],
                        "stored_path": a.get("stored_path", ""),
                        "resource_id": a.get("resource_id"),
                    }
                    for a in delivered_attachments
                ]

            # Issue #2193 PR5: deliver_gate.deliver() now owns auto-start/readiness,
            # AC5 sender-scoped flush, AC6 queue-pause gating, AC7 HALT/PIVOT
            # authority, AC8 auto_start enforcement, and the HALT/PIVOT interrupt
            # handling itself (strictly richer than this method's former dedicated
            # branches, which are deleted). dispatch_content/dispatch_metadata carry
            # the SDK-formatted message/metadata built above through to whichever
            # dispatch site actually sends it (inline, or later via the WHEN_IDLE
            # drain loop) — comm.content/comm.metadata themselves are left untouched
            # for any caller that inspects this Comm post-return.
            result = await self.system.session_port.delivery_gate.deliver(
                comm,
                dispatch_content=formatted_message,
                dispatch_metadata=comm_metadata,
            )
            if not result.success:
                legion_logger.error(
                    f"Failed to deliver comm {comm.comm_id} to minion {comm.to_minion_id}: {result.error}"
                )
                if comm.from_minion_id:
                    await self._send_system_error_comm(
                        to_minion_id=comm.from_minion_id,
                        error_message=f"Failed to deliver message: {result.error}",
                        original_comm_id=comm.comm_id
                    )
                return False

            legion_logger.info(f"Delivered comm {comm.comm_id} from {from_name} to minion {comm.to_minion_id}")
            return True

        except Exception as e:
            legion_logger.error(f"Failed to deliver comm {comm.comm_id} to minion {comm.to_minion_id}: {e}")
            # Send error comm back to sender about delivery failure
            if comm.from_minion_id:
                await self._send_system_error_comm(
                    to_minion_id=comm.from_minion_id,
                    error_message=f"Failed to deliver message: {str(e)}",
                    original_comm_id=comm.comm_id
                )
            return False

    async def _send_to_user(self, comm: Comm) -> bool:
        """
        Send Comm to user via WebSocket.

        Args:
            comm: Comm with to_user=True

        Returns:
            bool: True if sent successfully
        """
        # TODO: Phase 2 - Broadcast via WebSocket to UI
        return True

    async def _send_system_error_comm(
        self,
        to_minion_id: str,
        error_message: str,
        original_comm_id: str
    ) -> None:
        """
        Send a system-generated error Comm back to a minion.

        Args:
            to_minion_id: Target minion to receive error notification
            error_message: Human-readable error description
            original_comm_id: ID of the original comm that failed
        """
        try:
            error_comm = Comm(
                comm_id=str(uuid.uuid4()),
                from_minion_id=SYSTEM_MINION_ID,  # System-generated error
                from_minion_name=SYSTEM_MINION_NAME,
                from_user=False,
                to_minion_id=to_minion_id,
                to_user=False,
                content=error_message,
                comm_type=CommType.SYSTEM,  # Use SYSTEM for system-generated messages
                delivery=DeliveryPolicy.IMMEDIATE,
                in_reply_to=original_comm_id,  # Reference the failed comm
                visible_to_user=True
            )

            # Persist the error comm
            await self._persist_comm(error_comm)

            # Format system error message
            formatted_message = f"**🚨 System Error:**\n\n{error_message}\n\n_(Original comm ID: {original_comm_id})_"

            # Send to minion (but don't trigger auto-start to avoid loops)
            target_minion = await self.system.session_port.session_manager.get_session_info(to_minion_id)

            if target_minion and target_minion.state == SessionState.ACTIVE:
                # Issue #1779: system error comms aren't "user messages" — skip timestamp injection.
                await self.system.session_port.send_message(
                    session_id=to_minion_id,
                    message=formatted_message,
                    inject_timestamp=False,
                )
                legion_logger.info(f"Sent system error comm to minion {to_minion_id}")
            else:
                legion_logger.info(f"System error comm persisted for minion {to_minion_id} (not active, will see on next start)")

        except Exception as e:
            legion_logger.error(f"Failed to send system error comm to {to_minion_id}: {e}")

    async def _persist_comm(self, comm: Comm) -> None:
        """
        Persist Comm to appropriate locations.

        Broadcasts the comm to WebSocket clients watching the legion, pushes a
        UI notification event, and emits it to the audit writer.

        Args:
            comm: Comm to persist
        """
        # Get legion_id from source or destination
        legion_id = None
        from_minion_name = None
        to_minion_name = None

        if comm.from_minion_id:
            # Get minion info to find legion_id (project_id)
            minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
            if minion:
                legion_id = minion.project_id  # project_id IS the legion_id
                from_minion_name = minion.name

        if comm.to_minion_id:
            # Get minion info to find legion_id (project_id)
            minion = await self.system.legion_coordinator.get_minion_info(comm.to_minion_id)
            if minion:
                legion_id = minion.project_id  # project_id IS the legion_id
                to_minion_name = minion.name

        # If legion_id still not found and this is from user, need to get it from to_minion
        if not legion_id and comm.from_user:
            if comm.to_minion_id:
                minion = await self.system.legion_coordinator.get_minion_info(comm.to_minion_id)
                if minion:
                    legion_id = minion.project_id
                    if not to_minion_name:
                        to_minion_name = minion.name

        # Broadcast comm to WebSocket clients watching this legion
        if legion_id and self._comm_broadcast_callback:
            try:
                await self._comm_broadcast_callback(legion_id, comm)
            except Exception as e:
                legion_logger.error(f"Failed to broadcast comm {comm.comm_id} to WebSocket: {e}")

        # Issue #699: Push UI notification for user-facing comms
        # This fires on the global UI WebSocket so sounds play regardless of view.
        # Exclude SYSTEM/SPAWN/DISPOSE (internal lifecycle) comms.
        if legion_id and self._ui_notification_callback:
            if comm.comm_type not in (CommType.SYSTEM, CommType.SPAWN, CommType.DISPOSE):
                try:
                    await self._ui_notification_callback(comm)
                except Exception as e:
                    legion_logger.error(f"Failed to send UI notification for comm {comm.comm_id}: {e}")

        # Issue #1127: Emit comm to audit writer
        if self.audit_writer is not None:
            try:
                session_id = comm.from_minion_id or comm.to_minion_id or "user"
                comm_dict = {
                    "comm_id": comm.comm_id,
                    "comm_type": comm.comm_type.value if hasattr(comm.comm_type, "value") else str(comm.comm_type),
                    "from_minion_id": comm.from_minion_id,
                    "to_minion_id": comm.to_minion_id,
                    "from_minion_name": from_minion_name,
                    "to_minion_name": to_minion_name,
                    "summary": comm.summary,
                    "content": (comm.content or "")[:80] if comm.content else None,
                    "timestamp": comm.timestamp if hasattr(comm, "timestamp") else None,
                }
                await self.audit_writer.on_comm(
                    session_id=session_id,
                    project_id=legion_id,
                    legion_id=legion_id,
                    comm_data=comm_dict,
                )
            except Exception as e:
                legion_logger.error(f"Failed to emit comm to audit writer: {e}")

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert agent name to slug for consistent color hashing."""
        slug = name.lower().strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        return slug or 'unknown'

    def _extract_tags(self, content: str) -> tuple[list[str], list[str]]:
        """
        Extract #minion-name tags from content.

        Args:
            content: Message content

        Returns:
            Tuple of (minion_names, []) - second element kept empty for backward compatibility

        Examples:
            "#DatabaseExpert can you review this?" -> (["DatabaseExpert"], [])
            "Check with #Alice and #Bob" -> (["Alice", "Bob"], [])
        """
        # Pattern: # followed by word characters (letters, digits, underscores, hyphens)
        tags = re.findall(r'#([\w-]+)', content)

        # All tags are treated as potential minion references
        # Actual validation against known minions happens elsewhere
        minion_names = tags

        return (minion_names, [])

    async def _get_legion_id_for_comm(self, comm: Comm) -> str | None:
        """
        Extract legion_id from a comm by looking up the source or target minion.

        Args:
            comm: Comm object

        Returns:
            Legion UUID (project_id) or None if not a legion comm
        """
        # Try to get legion_id from sender minion
        if comm.from_minion_id and comm.from_minion_id != SYSTEM_MINION_ID:
            minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
            if minion:
                return minion.project_id

        # Try to get legion_id from target minion
        if comm.to_minion_id:
            minion = await self.system.legion_coordinator.get_minion_info(comm.to_minion_id)
            if minion:
                return minion.project_id

        return None

    async def _get_target_minions_for_comm(self, comm: Comm) -> list[str]:
        """
        Get list of minion IDs that should receive this comm.

        Handles direct minion-to-minion communication only.

        Args:
            comm: Comm object

        Returns:
            List of minion session IDs (single element for direct communication)
        """
        targets = []

        if comm.to_minion_id:
            # Direct to specific minion
            targets.append(comm.to_minion_id)

        return targets
