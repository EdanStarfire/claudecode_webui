"""
CommRouter - Communication routing for Legion multi-agent system.

Responsibilities:
- Convert Comm → Message for SDK injection
- Convert SDK Message → Comm for routing
- Route Comms to minions, channels, or user
- Handle interrupt priorities (Halt, Pivot)
- Persist Comms to multiple locations
- Parse #minion-name and #channel-name tags for explicit references
"""

import re
import json
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple, Optional
from datetime import datetime

from src.models.legion_models import Comm, CommType, InterruptPriority
from src.logging_config import get_logger

if TYPE_CHECKING:
    from src.legion_system import LegionSystem

# Legion logger
legion_logger = get_logger('legion', 'COMM_ROUTER')


class CommRouter:
    """Routes communications between minions, channels, and user."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize CommRouter with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

    async def route_comm(self, comm: Comm) -> bool:
        """
        Route a Comm to its destination(s).

        Args:
            comm: Comm object to route

        Returns:
            bool: True if routing succeeded

        Raises:
            ValueError: If comm validation fails
        """
        legion_logger.debug(f"Routing comm {comm.comm_id}: from={comm.from_minion_id}, to_minion={comm.to_minion_id}, to_channel={comm.to_channel_id}, to_user={comm.to_user}")

        # Validate comm has proper routing
        comm.validate()

        # Persist the comm
        await self._persist_comm(comm)
        legion_logger.debug(f"Comm {comm.comm_id} persisted successfully")

        # Route to destination
        if comm.to_minion_id:
            result = await self._send_to_minion(comm)
            legion_logger.info(f"Comm {comm.comm_id} routed to minion {comm.to_minion_id}: {'success' if result else 'failed'}")
            return result
        elif comm.to_channel_id:
            result = await self._broadcast_to_channel(comm)
            legion_logger.info(f"Comm {comm.comm_id} broadcast to channel {comm.to_channel_id}: {'success' if result else 'failed'}")
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
                        error_message=f"Failed to deliver message: Target minion not found",
                        original_comm_id=comm.comm_id
                    )
                return False

            # Auto-start minion if not active
            from src.session_manager import SessionState
            if target_minion.state not in [SessionState.ACTIVE, SessionState.STARTING]:
                legion_logger.info(f"Target minion {comm.to_minion_id} is in {target_minion.state} state - auto-starting")

                # Start the session
                success = await self.system.session_coordinator.start_session(
                    session_id=comm.to_minion_id,
                    permission_callback=None  # Use default permission callback
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
            from_name = "User"
            if comm.from_minion_id:
                from_minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
                if from_minion and from_minion.name:
                    from_name = from_minion.name

            # Format message for recipient minion
            comm_type_prefix = {
                CommType.TASK: "📋 Task",
                CommType.QUESTION: "❓ Question",
                CommType.REPORT: "📊 Report",
                CommType.GUIDE: "💡 Guide"
            }.get(comm.comm_type, "💬 Message")

            # Use summary in header if available, otherwise use truncated content
            header_summary = comm.summary if comm.summary else (comm.content[:50] + "..." if len(comm.content) > 50 else comm.content)
            formatted_message = f"**{comm_type_prefix} from {from_name}:** {header_summary}\n\n{comm.content}"

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

    async def _broadcast_to_channel(self, comm: Comm) -> bool:
        """
        Broadcast Comm to all members of a channel.

        Args:
            comm: Comm with to_channel_id set

        Returns:
            bool: True if broadcast succeeded
        """
        # TODO: Phase 2 - Look up channel members and route to each
        return True

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
                from_minion_id="LEGION_SYSTEM",  # Special system identifier
                from_user=False,
                to_minion_id=to_minion_id,
                to_user=False,
                content=error_message,
                comm_type=CommType.REPORT,  # Use REPORT for system messages
                interrupt_priority=InterruptPriority.ROUTINE,
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
        1. Source minion's comm log (if from minion)
        2. Destination minion's comm log (if to minion)
        3. Channel's comm log (if to channel)

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

        if comm.to_channel_id:
            # Get channel info to find legion_id
            channel = await self.system.channel_manager.get_channel(comm.to_channel_id)
            if channel:
                legion_id = channel.legion_id
                # Persist to channel's log
                await self._append_to_comm_log(
                    legion_id,
                    f"channels/{comm.to_channel_id}",
                    comm
                )

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

    def _extract_tags(self, content: str) -> Tuple[List[str], List[str]]:
        """
        Extract #minion-name and #channel-name tags from content.

        Args:
            content: Message content

        Returns:
            Tuple of (minion_names, channel_names)

        Examples:
            "#DatabaseExpert can you review this?" -> (["DatabaseExpert"], [])
            "Posted in #planning channel" -> ([], ["planning"])
            "Check with #Alice and #Bob in #coordination" -> (["Alice", "Bob"], ["coordination"])
        """
        # Pattern: # followed by word characters (letters, digits, underscores, hyphens)
        tags = re.findall(r'#([\w-]+)', content)

        # Separate into minion and channel tags
        # For now, we'll need to check against actual minion/channel names
        # Simplified: assume lowercase names are channels, CamelCase are minions
        minion_names = []
        channel_names = []

        for tag in tags:
            # Check if it matches a known minion (has uppercase)
            if any(c.isupper() for c in tag):
                minion_names.append(tag)
            else:
                channel_names.append(tag)

        return (minion_names, channel_names)
