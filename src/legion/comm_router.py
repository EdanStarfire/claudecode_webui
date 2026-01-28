"""
CommRouter - Communication routing for Legion multi-agent system.

Responsibilities:
- Convert Comm → Message for SDK injection
- Convert SDK Message → Comm for routing
- Route Comms to minions or user (direct communication only)
- Handle interrupt priorities (Halt, Pivot)
- Persist Comms to legion timeline and minion-specific logs
- Parse #minion-name tags for explicit references
"""

import json
import re
from typing import TYPE_CHECKING

from src.logging_config import get_logger
from src.models.legion_models import (
    SYSTEM_MINION_ID,
    SYSTEM_MINION_NAME,
    Comm,
    CommType,
    InterruptPriority,
)

if TYPE_CHECKING:
    from src.legion_system import LegionSystem

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

    async def get_visible_minions(self, caller_id: str) -> list[str]:
        """
        Return minion IDs visible to the caller based on immediate hierarchy group.

        A minion can see:
        1. Direct children (if overseer)
        2. Direct parent (overseer that spawned it)
        3. Direct siblings (other children of same parent)

        The user always sees all minions (handled separately).

        Args:
            caller_id: Session ID of the calling minion

        Returns:
            List of visible minion session IDs
        """
        session_manager = self.system.session_coordinator.session_manager
        caller = await session_manager.get_session_info(caller_id)
        if not caller:
            return []

        visible = set()

        # Direct children
        if caller.child_minion_ids:
            visible.update(caller.child_minion_ids)

        # Parent overseer + siblings
        if caller.parent_overseer_id:
            visible.add(caller.parent_overseer_id)

            # Siblings: other children of the same parent
            parent = await session_manager.get_session_info(caller.parent_overseer_id)
            if parent and parent.child_minion_ids:
                visible.update(parent.child_minion_ids)

        # Exclude self
        visible.discard(caller_id)

        return list(visible)

    async def validate_comm_target(self, sender_id: str, recipient_id: str) -> bool:
        """
        Check if sender can directly communicate with recipient.

        User (sender_id=None or "user") can always reach anyone.
        Minions can only reach their immediate hierarchy group.

        Args:
            sender_id: Session ID of sender (None for user)
            recipient_id: Session ID of recipient

        Returns:
            True if sender can comm recipient
        """
        if not sender_id or sender_id == "user":
            return True
        return recipient_id in await self.get_visible_minions(sender_id)

    def set_comm_broadcast_callback(self, callback):
        """
        Set callback for broadcasting new comms to WebSocket clients.

        Args:
            callback: Async function(legion_id, comm) to broadcast comm events
        """
        self._comm_broadcast_callback = callback

    async def route_comm(self, comm: Comm) -> bool:
        """
        Route a Comm to its destination(s).

        If legion is in emergency halt, queues comm instead of routing.

        Args:
            comm: Comm object to route

        Returns:
            bool: True if routing succeeded (or queued during halt)

        Raises:
            ValueError: If comm validation fails or queue overflow
        """
        legion_logger.debug(f"Routing comm {comm.comm_id}: from={comm.from_minion_id}, to_minion={comm.to_minion_id}, to_user={comm.to_user}")

        # Validate comm has proper routing
        comm.validate()

        # Check for emergency halt - queue instead of routing
        legion_id = await self._get_legion_id_for_comm(comm)
        if legion_id:
            coordinator = self.system.legion_coordinator
            if coordinator.emergency_halt_active.get(legion_id, False):
                # Legion is halted - queue this comm
                legion_logger.info(f"Legion {legion_id} in emergency halt - queuing comm {comm.comm_id}")
                return await self._queue_comm_during_halt(legion_id, comm)

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

    async def _send_to_minion(self, comm: Comm) -> bool:
        """
        Send Comm to a specific minion by injecting message into SDK session.
        Auto-starts the minion session if it's not active.

        Args:
            comm: Comm with to_minion_id set

        Returns:
            bool: True if sent successfully
        """
        try:
            # Check target minion state
            target_minion = await self.system.session_coordinator.session_manager.get_session_info(comm.to_minion_id)
            if not target_minion:
                legion_logger.error(f"Target minion {comm.to_minion_id} not found")
                # Send error comm back to sender
                if comm.from_minion_id:
                    await self._send_system_error_comm(
                        to_minion_id=comm.from_minion_id,
                        error_message="Failed to deliver message: Target minion not found",
                        original_comm_id=comm.comm_id
                    )
                return False

            # Auto-start minion if not active
            from src.session_manager import SessionState
            if target_minion.state not in [SessionState.ACTIVE, SessionState.STARTING]:
                legion_logger.info(f"Target minion {comm.to_minion_id} is in {target_minion.state} state - auto-starting")

                # Register WebSocket message callback before starting
                # This ensures messages from the auto-started minion broadcast to WebSocket clients
                if self.system.message_callback_registrar:
                    self.system.message_callback_registrar(comm.to_minion_id)
                    legion_logger.info(f"Registered WebSocket message callback for auto-started minion {comm.to_minion_id}")
                else:
                    legion_logger.warning(f"No message callback registrar available for auto-start of {comm.to_minion_id}")

                # Start the session with permission callback
                # Create permission callback using factory (same pattern as user-created minions)
                permission_callback = None
                if self.system.permission_callback_factory:
                    permission_callback = self.system.permission_callback_factory(comm.to_minion_id)
                    legion_logger.info(f"Created permission callback for auto-started minion {comm.to_minion_id}")
                else:
                    legion_logger.warning(f"No permission callback factory available for auto-start of {comm.to_minion_id}")

                success = await self.system.session_coordinator.start_session(
                    session_id=comm.to_minion_id,
                    permission_callback=permission_callback
                )

                if not success:
                    legion_logger.error(f"Failed to auto-start minion {comm.to_minion_id}")
                    # Send error comm back to sender
                    if comm.from_minion_id:
                        await self._send_system_error_comm(
                            to_minion_id=comm.from_minion_id,
                            error_message=f"Failed to deliver message: Could not auto-start target minion (state: {target_minion.state})",
                            original_comm_id=comm.comm_id
                        )
                    return False

                # Wait for session to become active (with timeout)
                import asyncio
                max_wait = 30  # 30 seconds timeout
                wait_interval = 0.5
                elapsed = 0

                while elapsed < max_wait:
                    session_info = await self.system.session_coordinator.session_manager.get_session_info(comm.to_minion_id)
                    if session_info and session_info.state == SessionState.ACTIVE:
                        legion_logger.info(f"Minion {comm.to_minion_id} is now active after {elapsed}s")
                        break

                    await asyncio.sleep(wait_interval)
                    elapsed += wait_interval
                else:
                    legion_logger.warning(f"Minion {comm.to_minion_id} did not become active within {max_wait}s - sending error to sender")
                    # Send timeout error comm back to sender
                    if comm.from_minion_id:
                        await self._send_system_error_comm(
                            to_minion_id=comm.from_minion_id,
                            error_message=f"Failed to deliver message: Target minion did not start within {max_wait} seconds (state: {session_info.state if session_info else 'unknown'})",
                            original_comm_id=comm.comm_id
                        )
                    return False

            # Get sender name for formatting
            # Use "Minion #user" to signal that replies should use send_comm MCP tool
            from_name = "Minion #user"
            is_from_user = not comm.from_minion_id  # Track if message is from user

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

            # Only add send_comm instruction if message is from user, not from other minions
            if is_from_user:
                # Direct message from user - instruct to reply to user
                formatted_message = f"**{comm_type_prefix} from {from_name}:** {header_summary}\n\n{comm.content}\n\n---\n**Please respond using the `send_comm` tool to send your reply back to {from_name}.**"
            else:
                # Message from another minion
                formatted_message = f"**{comm_type_prefix} from {from_name}:** {header_summary}\n\n{comm.content}"

            # Handle interrupt priority
            if comm.interrupt_priority in [InterruptPriority.HALT, InterruptPriority.PIVOT]:
                legion_logger.info(f"Comm {comm.comm_id} has {comm.interrupt_priority.value} priority - interrupting session {comm.to_minion_id}")

                # Interrupt the target session
                try:
                    await self.system.session_coordinator.interrupt_session(comm.to_minion_id)
                    legion_logger.debug(f"Successfully interrupted session {comm.to_minion_id}")
                except Exception as e:
                    legion_logger.warning(f"Failed to interrupt session {comm.to_minion_id}: {e}")
                    # Continue anyway - message will queue if interrupt fails

            # Send message to target minion via SessionCoordinator
            await self.system.session_coordinator.send_message(
                session_id=comm.to_minion_id,
                message=formatted_message
            )

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
        import uuid

        try:
            # Create system error comm
            error_comm = Comm(
                comm_id=str(uuid.uuid4()),
                from_minion_id=SYSTEM_MINION_ID,  # System-generated error
                from_minion_name=SYSTEM_MINION_NAME,
                from_user=False,
                to_minion_id=to_minion_id,
                to_user=False,
                content=error_message,
                comm_type=CommType.SYSTEM,  # Use SYSTEM for system-generated messages
                interrupt_priority=InterruptPriority.NONE,
                in_reply_to=original_comm_id,  # Reference the failed comm
                visible_to_user=True
            )

            # Persist the error comm
            await self._persist_comm(error_comm)

            # Format system error message
            formatted_message = f"**🚨 System Error:**\n\n{error_message}\n\n_(Original comm ID: {original_comm_id})_"

            # Send to minion (but don't trigger auto-start to avoid loops)
            target_minion = await self.system.session_coordinator.session_manager.get_session_info(to_minion_id)
            from src.session_manager import SessionState

            if target_minion and target_minion.state == SessionState.ACTIVE:
                await self.system.session_coordinator.send_message(
                    session_id=to_minion_id,
                    message=formatted_message
                )
                legion_logger.info(f"Sent system error comm to minion {to_minion_id}")
            else:
                legion_logger.info(f"System error comm persisted for minion {to_minion_id} (not active, will see on next start)")

        except Exception as e:
            legion_logger.error(f"Failed to send system error comm to {to_minion_id}: {e}")

    async def _persist_comm(self, comm: Comm) -> None:
        """
        Persist Comm to appropriate locations.

        Comms are stored in:
        1. Legion timeline (main timeline.jsonl)
        2. Source minion's comm log (if from minion)
        3. Destination minion's comm log (if to minion)

        Args:
            comm: Comm to persist
        """
        # Get legion_id from source or destination
        legion_id = None

        if comm.from_minion_id:
            # Get minion info to find legion_id (project_id)
            minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
            if minion:
                legion_id = minion.project_id  # project_id IS the legion_id
                # Persist to source minion's log
                await self._append_to_comm_log(
                    legion_id,
                    f"minions/{comm.from_minion_id}",
                    comm
                )

        if comm.to_minion_id:
            # Get minion info to find legion_id (project_id)
            minion = await self.system.legion_coordinator.get_minion_info(comm.to_minion_id)
            if minion:
                legion_id = minion.project_id  # project_id IS the legion_id
                # Persist to destination minion's log
                await self._append_to_comm_log(
                    legion_id,
                    f"minions/{comm.to_minion_id}",
                    comm
                )

        # If legion_id still not found and this is from user, need to get it from to_minion
        if not legion_id and comm.from_user:
            if comm.to_minion_id:
                minion = await self.system.legion_coordinator.get_minion_info(comm.to_minion_id)
                if minion:
                    legion_id = minion.project_id

        # ALWAYS persist to main legion timeline (this was missing!)
        if legion_id:
            await self._append_to_timeline(legion_id, comm)

        # Broadcast comm to WebSocket clients watching this legion
        if legion_id and self._comm_broadcast_callback:
            try:
                await self._comm_broadcast_callback(legion_id, comm)
            except Exception as e:
                legion_logger.error(f"Failed to broadcast comm {comm.comm_id} to WebSocket: {e}")

    async def _append_to_comm_log(
        self,
        legion_id: str,
        subpath: str,
        comm: Comm
    ) -> None:
        """
        Append Comm to a JSONL log file.

        Args:
            legion_id: Legion ID
            subpath: Subpath within legion directory (e.g., "minions/{minion_id}")
            comm: Comm to append
        """
        # Get data_dir from SessionCoordinator
        data_dir = self.system.session_coordinator.data_dir

        # Construct path: {data_dir}/legions/{legion_id}/{subpath}/comms.jsonl
        log_dir = data_dir / "legions" / legion_id / subpath
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / "comms.jsonl"

        # Append comm as JSON line
        with open(log_file, "a", encoding="utf-8") as f:
            json.dump(comm.to_dict(), f)
            f.write("\n")

        legion_logger.debug(f"Appended comm {comm.comm_id} to {log_file}")

    async def _append_to_timeline(self, legion_id: str, comm: Comm) -> None:
        """
        Append Comm to the main legion timeline.jsonl file.

        Args:
            legion_id: Legion ID
            comm: Comm to append
        """
        # Get data_dir from SessionCoordinator
        data_dir = self.system.session_coordinator.data_dir

        # Construct path: {data_dir}/legions/{legion_id}/timeline.jsonl
        legion_dir = data_dir / "legions" / legion_id
        legion_dir.mkdir(parents=True, exist_ok=True)

        timeline_file = legion_dir / "timeline.jsonl"

        # Append comm as JSON line
        with open(timeline_file, "a", encoding="utf-8") as f:
            json.dump(comm.to_dict(), f)
            f.write("\n")

        legion_logger.debug(f"Appended comm {comm.comm_id} to timeline {timeline_file}")

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

    async def _queue_comm_during_halt(self, legion_id: str, comm: Comm) -> bool:
        """
        Queue comm during emergency halt.

        Organizes queue by recipient minion for FIFO delivery per minion.

        Args:
            legion_id: Legion UUID
            comm: Comm to queue

        Returns:
            True if queued successfully

        Raises:
            ValueError: If queue size exceeds limit (10,000 comms)
        """
        coordinator = self.system.legion_coordinator

        # Get or create queue for this legion
        if legion_id not in coordinator.halted_comm_queues:
            coordinator.halted_comm_queues[legion_id] = {}

        legion_queue = coordinator.halted_comm_queues[legion_id]

        # Determine target minion(s)
        target_minions = await self._get_target_minions_for_comm(comm)

        # Queue for each target minion
        for minion_id in target_minions:
            if minion_id not in legion_queue:
                legion_queue[minion_id] = []

            legion_queue[minion_id].append(comm)

        # Check total queue size across all minions
        total_queued = sum(len(q) for q in legion_queue.values())

        if total_queued > coordinator.MAX_QUEUED_COMMS:
            # Remove the comm we just added (overflow)
            for minion_id in target_minions:
                if legion_queue[minion_id] and legion_queue[minion_id][-1] == comm:
                    legion_queue[minion_id].pop()

            raise ValueError(
                f"Comm queue overflow for legion {legion_id}: "
                f"{total_queued} comms queued (limit: {coordinator.MAX_QUEUED_COMMS}). "
                f"Latest comm dropped."
            )

        # Log queuing
        legion_logger.info(
            f"Queued comm {comm.comm_id} during emergency halt "
            f"(total: {total_queued}, targets: {len(target_minions)})"
        )

        return True

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
