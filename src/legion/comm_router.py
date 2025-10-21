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

        Args:
            comm: Comm with to_minion_id set

        Returns:
            bool: True if sent successfully
        """
        try:
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

            formatted_message = f"**{comm_type_prefix} from {from_name}:**\n\n{comm.content}"

            # Send message to target minion via SessionCoordinator
            await self.system.session_coordinator.send_message(
                session_id=comm.to_minion_id,
                message=formatted_message
            )

            legion_logger.info(f"Delivered comm {comm.comm_id} from {from_name} to minion {comm.to_minion_id}")
            return True

        except Exception as e:
            legion_logger.error(f"Failed to deliver comm {comm.comm_id} to minion {comm.to_minion_id}: {e}")
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
