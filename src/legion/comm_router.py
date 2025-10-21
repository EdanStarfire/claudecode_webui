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

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


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
        # Validate comm has proper routing
        comm.validate()

        # Persist the comm
        await self._persist_comm(comm)

        # Route to destination
        if comm.to_minion_id:
            return await self._send_to_minion(comm)
        elif comm.to_channel_id:
            return await self._broadcast_to_channel(comm)
        elif comm.to_user:
            return await self._send_to_user(comm)

        return False

    async def _send_to_minion(self, comm: Comm) -> bool:
        """
        Send Comm to a specific minion.

        Args:
            comm: Comm with to_minion_id set

        Returns:
            bool: True if sent successfully
        """
        # TODO: Phase 2 - Inject message into minion's SDK session
        # For now, just persist it
        return True

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
            # Get minion info to find legion_id
            minion = await self.system.legion_coordinator.get_minion_info(comm.from_minion_id)
            if minion:
                legion_id = minion.legion_id
                # Persist to source minion's log
                await self._append_to_comm_log(
                    legion_id,
                    f"minions/{comm.from_minion_id}",
                    comm
                )

        if comm.to_minion_id:
            # Get minion info to find legion_id
            minion = await self.system.legion_coordinator.get_minion_info(comm.to_minion_id)
            if minion:
                legion_id = minion.legion_id
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
        # Construct path: data/legions/{legion_id}/{subpath}/comms.jsonl
        log_dir = Path("data") / "legions" / legion_id / subpath
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / "comms.jsonl"

        # Append comm as JSON line
        with open(log_file, "a", encoding="utf-8") as f:
            json.dump(comm.to_dict(), f)
            f.write("\n")

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
