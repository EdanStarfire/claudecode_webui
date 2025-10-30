"""
ChannelManager - Channel management for Legion multi-agent system.

Responsibilities:
- Create/delete channels
- Manage channel membership (add/remove minions)
- Coordinate channel broadcasts
- NOTE: Gossip search deferred to post-MVP (using central registry instead)
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from src.legion_system import LegionSystem
    from src.models.legion_models import Channel


class ChannelManager:
    """Manages channels and membership for cross-horde collaboration."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize ChannelManager with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

    async def get_channel(self, channel_id: str) -> Optional['Channel']:
        """
        Get channel by ID.

        Args:
            channel_id: Channel UUID

        Returns:
            Channel if found, None otherwise
        """
        return self.system.legion_coordinator.channels.get(channel_id)

    async def create_channel(
        self,
        legion_id: str,
        name: str,
        description: str,
        purpose: str,
        member_minion_ids: Optional[List[str]] = None,
        created_by_minion_id: Optional[str] = None
    ) -> str:
        """
        Create a new channel with name, purpose, and description.

        Args:
            legion_id: Legion UUID
            name: Channel name
            description: Purpose description
            purpose: Purpose type (e.g., "coordination", "planning", "scene", "research")
            member_minion_ids: List of minion IDs to add as members (default: empty list)
            created_by_minion_id: Minion ID that created channel (None if user-created)

        Returns:
            channel_id: UUID of created channel

        Raises:
            ValueError: If legion doesn't exist, channel name already exists, or invalid member IDs
        """
        # Validate legion exists
        legion = await self.system.legion_coordinator.get_legion(legion_id)
        if not legion:
            raise ValueError(f"Legion {legion_id} does not exist")

        # Check channel name uniqueness per-legion
        existing_channels = await self.list_channels(legion_id)
        for channel in existing_channels:
            if channel.name == name:
                raise ValueError(f"Channel with name '{name}' already exists in legion {legion_id}")

        # Validate member minion IDs exist
        member_minion_ids = member_minion_ids or []
        for minion_id in member_minion_ids:
            minion = await self.system.legion_coordinator.get_minion_info(minion_id)
            if not minion:
                raise ValueError(f"Minion {minion_id} does not exist")

        # Create Channel instance
        from src.models.legion_models import Channel

        channel_id = str(uuid.uuid4())

        channel = Channel(
            channel_id=channel_id,
            legion_id=legion_id,
            name=name,
            description=description,
            purpose=purpose,
            member_minion_ids=member_minion_ids.copy(),
            created_by_minion_id=created_by_minion_id,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        # Add to coordinator's channels dict
        self.system.legion_coordinator.channels[channel_id] = channel

        # Persist to disk
        await self._persist_channel(channel)

        return channel_id

    async def add_member(self, channel_id: str, minion_id: str) -> None:
        """
        Add a member to a channel.

        Args:
            channel_id: Channel UUID
            minion_id: Minion UUID to add

        Raises:
            KeyError: If channel does not exist
            ValueError: If minion doesn't exist or is already a member
        """
        # Get channel
        channel = self.system.legion_coordinator.channels.get(channel_id)
        if not channel:
            raise KeyError(f"Channel {channel_id} does not exist")

        # Validate minion exists
        minion = await self.system.legion_coordinator.get_minion_info(minion_id)
        if not minion:
            raise ValueError(f"Minion {minion_id} does not exist")

        # Check not already member (idempotent - return success if already member)
        if minion_id in channel.member_minion_ids:
            return  # Already a member - idempotent success

        # Add member
        channel.member_minion_ids.append(minion_id)
        channel.updated_at = datetime.now()

        # Persist to disk
        await self._persist_channel(channel)

    async def remove_member(self, channel_id: str, minion_id: str) -> None:
        """
        Remove a member from a channel.

        Args:
            channel_id: Channel UUID
            minion_id: Minion UUID to remove

        Raises:
            KeyError: If channel does not exist
            ValueError: If minion is not a member
        """
        # Get channel
        channel = self.system.legion_coordinator.channels.get(channel_id)
        if not channel:
            raise KeyError(f"Channel {channel_id} does not exist")

        # Check minion is member (idempotent - return success if not a member)
        if minion_id not in channel.member_minion_ids:
            return  # Already not a member - idempotent success

        # Remove member
        channel.member_minion_ids.remove(minion_id)
        channel.updated_at = datetime.now()

        # Persist to disk
        await self._persist_channel(channel)

    async def delete_channel(self, channel_id: str) -> None:
        """
        Delete a channel and its associated files.

        Minion cleanup is handled by business logic (not ChannelManager's responsibility).

        Args:
            channel_id: Channel UUID

        Raises:
            KeyError: If channel does not exist
        """
        # Get channel
        channel = self.system.legion_coordinator.channels.get(channel_id)
        if not channel:
            raise KeyError(f"Channel {channel_id} does not exist")

        # Remove from coordinator's dict
        del self.system.legion_coordinator.channels[channel_id]

        # Delete channel directory and all files
        data_dir = self.system.session_coordinator.data_dir
        channel_dir = data_dir / "legions" / channel.legion_id / "channels" / channel_id

        if channel_dir.exists():
            import shutil
            shutil.rmtree(channel_dir)

    async def list_channels(self, legion_id: str) -> List['Channel']:
        """
        List all channels for a specific legion.

        Args:
            legion_id: Legion UUID

        Returns:
            List of Channel objects for this legion
        """
        channels = []
        for channel in self.system.legion_coordinator.channels.values():
            if channel.legion_id == legion_id:
                channels.append(channel)
        return channels

    async def _persist_channel(self, channel: 'Channel') -> None:
        """
        Persist channel to disk.

        Creates directory structure and writes channel_state.json.

        Args:
            channel: Channel instance to persist
        """
        # Get data directory
        data_dir = self.system.session_coordinator.data_dir
        channel_dir = data_dir / "legions" / channel.legion_id / "channels" / channel.channel_id

        # Create directory
        channel_dir.mkdir(parents=True, exist_ok=True)

        # Write channel state
        channel_file = channel_dir / "channel_state.json"
        with open(channel_file, 'w', encoding='utf-8') as f:
            json.dump(channel.to_dict(), f, indent=2)

    async def _load_channels_for_legion(self, legion_id: str) -> None:
        """
        Load all channels for a legion from disk into memory.

        Called at legion startup to restore channel state.

        Args:
            legion_id: Legion UUID
        """
        from src.models.legion_models import Channel

        # Get channels directory
        data_dir = self.system.session_coordinator.data_dir
        channels_dir = data_dir / "legions" / legion_id / "channels"

        if not channels_dir.exists():
            return

        # Scan for channel directories
        for channel_dir in channels_dir.iterdir():
            if not channel_dir.is_dir():
                continue

            # Read channel_state.json
            channel_file = channel_dir / "channel_state.json"
            if not channel_file.exists():
                print(f"Warning: Channel directory {channel_dir.name} missing channel_state.json")
                continue

            try:
                with open(channel_file, 'r', encoding='utf-8') as f:
                    channel_data = json.load(f)

                # Deserialize channel
                channel = Channel.from_dict(channel_data)

                # Add to coordinator's channels dict
                self.system.legion_coordinator.channels[channel.channel_id] = channel

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                print(f"Error loading channel from {channel_file}: {e}")
