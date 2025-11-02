"""
Tests for ChannelManager core functionality.

Tests cover:
- Channel creation with validation
- Member management (add/remove)
- Channel deletion
- Persistence and loading
- Edge cases (duplicate names, invalid IDs, etc.)
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.legion_system import LegionSystem


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_session_coordinator(temp_data_dir):
    """Create mock SessionCoordinator with data_dir."""
    mock = Mock()
    mock.data_dir = temp_data_dir
    return mock


@pytest.fixture
def mock_project_manager():
    """Create mock ProjectManager."""
    mock = Mock()

    # Mock get_project to return a legion
    async def mock_get_project(project_id):
        if project_id == "legion-123":
            mock_project = Mock()
            mock_project.project_id = "legion-123"
            mock_project.is_multi_agent = True
            return mock_project
        return None

    mock.get_project = AsyncMock(side_effect=mock_get_project)
    return mock


@pytest.fixture
def mock_session_manager():
    """Create mock SessionManager."""
    mock = Mock()

    # Mock get_session_info to return minions
    async def mock_get_session_info(session_id):
        if session_id in ["minion-1", "minion-2", "minion-3"]:
            mock_session = Mock()
            mock_session.session_id = session_id
            mock_session.is_minion = True
            mock_session.channel_ids = []  # Add channel_ids list
            return mock_session
        return None

    mock.get_session_info = AsyncMock(side_effect=mock_get_session_info)
    # Mock _persist_session_state (called when updating minion's channel list)
    mock._persist_session_state = AsyncMock()
    return mock


@pytest.fixture
def legion_system(mock_session_coordinator, mock_project_manager, mock_session_manager):
    """Create LegionSystem with mocked dependencies."""
    system = LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Override project_manager and session_manager accessors
    system.session_coordinator.project_manager = mock_project_manager
    system.session_coordinator.session_manager = mock_session_manager

    return system


@pytest.fixture
def channel_manager(legion_system):
    """Create ChannelManager instance."""
    return legion_system.channel_manager


# ==================== CREATE CHANNEL TESTS ====================

@pytest.mark.asyncio
async def test_create_channel_success(channel_manager):
    """Test successful channel creation."""
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test channel for coordination",
        purpose="coordination",
        member_minion_ids=["minion-1", "minion-2"]
    )

    # Verify channel was created
    assert channel_id is not None
    channel = await channel_manager.get_channel(channel_id)
    assert channel is not None
    assert channel.name == "test-channel"
    assert channel.description == "Test channel for coordination"
    assert channel.purpose == "coordination"
    assert "minion-1" in channel.member_minion_ids
    assert "minion-2" in channel.member_minion_ids


@pytest.mark.asyncio
async def test_create_channel_with_no_members(channel_manager):
    """Test channel creation with empty member list."""
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="empty-channel",
        description="Channel with no initial members",
        purpose="planning"
    )

    channel = await channel_manager.get_channel(channel_id)
    assert channel is not None
    assert len(channel.member_minion_ids) == 0


@pytest.mark.asyncio
async def test_create_channel_duplicate_name_raises_error(channel_manager):
    """Test that duplicate channel names raise ValueError."""
    # Create first channel
    await channel_manager.create_channel(
        legion_id="legion-123",
        name="duplicate-name",
        description="First channel",
        purpose="coordination"
    )

    # Attempt to create second channel with same name
    with pytest.raises(ValueError, match="already exists"):
        await channel_manager.create_channel(
            legion_id="legion-123",
            name="duplicate-name",
            description="Second channel",
            purpose="coordination"
        )


@pytest.mark.asyncio
async def test_create_channel_invalid_legion_raises_error(channel_manager):
    """Test that invalid legion ID raises ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        await channel_manager.create_channel(
            legion_id="invalid-legion",
            name="test-channel",
            description="Test",
            purpose="coordination"
        )


@pytest.mark.asyncio
async def test_create_channel_invalid_member_raises_error(channel_manager):
    """Test that invalid member ID raises ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        await channel_manager.create_channel(
            legion_id="legion-123",
            name="test-channel",
            description="Test",
            purpose="coordination",
            member_minion_ids=["invalid-minion"]
        )


# ==================== ADD MEMBER TESTS ====================

@pytest.mark.asyncio
async def test_add_member_success(channel_manager):
    """Test successful member addition."""
    # Create channel with one member
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1"]
    )

    # Add another member
    await channel_manager.add_member(channel_id, "minion-2")

    # Verify member was added
    channel = await channel_manager.get_channel(channel_id)
    assert "minion-2" in channel.member_minion_ids
    assert len(channel.member_minion_ids) == 2


@pytest.mark.asyncio
async def test_add_member_duplicate_is_idempotent(channel_manager):
    """Test that adding duplicate member is idempotent (no error)."""
    # Create channel with member
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1"]
    )

    # Add same member again - should succeed idempotently
    await channel_manager.add_member(channel_id, "minion-1")

    # Verify member still in channel (not duplicated)
    channel = await channel_manager.get_channel(channel_id)
    assert channel.member_minion_ids.count("minion-1") == 1


@pytest.mark.asyncio
async def test_add_member_invalid_channel_raises_error(channel_manager):
    """Test that invalid channel ID raises KeyError."""
    with pytest.raises(KeyError, match="does not exist"):
        await channel_manager.add_member("invalid-channel", "minion-1")


@pytest.mark.asyncio
async def test_add_member_invalid_minion_raises_error(channel_manager):
    """Test that invalid minion ID raises ValueError."""
    # Create channel
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination"
    )

    # Attempt to add invalid minion
    with pytest.raises(ValueError, match="does not exist"):
        await channel_manager.add_member(channel_id, "invalid-minion")


# ==================== REMOVE MEMBER TESTS ====================

@pytest.mark.asyncio
async def test_remove_member_success(channel_manager):
    """Test successful member removal."""
    # Create channel with two members
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1", "minion-2"]
    )

    # Remove one member
    await channel_manager.remove_member(channel_id, "minion-1")

    # Verify member was removed
    channel = await channel_manager.get_channel(channel_id)
    assert "minion-1" not in channel.member_minion_ids
    assert "minion-2" in channel.member_minion_ids
    assert len(channel.member_minion_ids) == 1


@pytest.mark.asyncio
async def test_remove_member_not_member_is_idempotent(channel_manager):
    """Test that removing non-member is idempotent (no error)."""
    # Create channel
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1"]
    )

    # Remove non-member - should succeed idempotently
    await channel_manager.remove_member(channel_id, "minion-2")

    # Verify original member still in channel
    channel = await channel_manager.get_channel(channel_id)
    assert "minion-1" in channel.member_minion_ids
    assert len(channel.member_minion_ids) == 1


@pytest.mark.asyncio
async def test_remove_member_invalid_channel_raises_error(channel_manager):
    """Test that invalid channel ID raises KeyError."""
    with pytest.raises(KeyError, match="does not exist"):
        await channel_manager.remove_member("invalid-channel", "minion-1")


# ==================== DELETE CHANNEL TESTS ====================

@pytest.mark.asyncio
async def test_delete_channel_success(channel_manager, temp_data_dir):
    """Test successful channel deletion."""
    # Create channel
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination"
    )

    # Verify channel exists
    channel = await channel_manager.get_channel(channel_id)
    assert channel is not None

    # Delete channel
    await channel_manager.delete_channel(channel_id)

    # Verify channel was removed from memory
    channel = await channel_manager.get_channel(channel_id)
    assert channel is None

    # Verify channel directory was deleted
    channel_dir = temp_data_dir / "legions" / "legion-123" / "channels" / channel_id
    assert not channel_dir.exists()


@pytest.mark.asyncio
async def test_delete_channel_invalid_raises_error(channel_manager):
    """Test that deleting invalid channel raises KeyError."""
    with pytest.raises(KeyError, match="does not exist"):
        await channel_manager.delete_channel("invalid-channel")


# ==================== LIST CHANNELS TESTS ====================

@pytest.mark.asyncio
async def test_list_channels_empty(channel_manager):
    """Test listing channels when none exist."""
    channels = await channel_manager.list_channels("legion-123")
    assert len(channels) == 0


@pytest.mark.asyncio
async def test_list_channels_single_legion(channel_manager):
    """Test listing channels for specific legion."""
    # Create two channels for legion-123
    await channel_manager.create_channel(
        legion_id="legion-123",
        name="channel-1",
        description="First channel",
        purpose="coordination"
    )
    await channel_manager.create_channel(
        legion_id="legion-123",
        name="channel-2",
        description="Second channel",
        purpose="planning"
    )

    # List channels
    channels = await channel_manager.list_channels("legion-123")
    assert len(channels) == 2
    channel_names = [c.name for c in channels]
    assert "channel-1" in channel_names
    assert "channel-2" in channel_names


# ==================== PERSISTENCE TESTS ====================

@pytest.mark.asyncio
async def test_persist_channel_creates_files(channel_manager, temp_data_dir):
    """Test that channel persistence creates directory and JSON file."""
    # Create channel
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1"]
    )

    # Verify directory and file were created
    channel_dir = temp_data_dir / "legions" / "legion-123" / "channels" / channel_id
    assert channel_dir.exists()

    channel_file = channel_dir / "channel_state.json"
    assert channel_file.exists()

    # Verify JSON content
    with open(channel_file, encoding='utf-8') as f:
        data = json.load(f)

    assert data['channel_id'] == channel_id
    assert data['name'] == "test-channel"
    assert data['legion_id'] == "legion-123"
    assert "minion-1" in data['member_minion_ids']


@pytest.mark.asyncio
async def test_load_channels_for_legion(channel_manager, temp_data_dir):
    """Test loading channels from disk."""
    # Create channel and persist
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1"]
    )

    # Clear in-memory channels
    channel_manager.system.legion_coordinator.channels.clear()

    # Load channels from disk
    await channel_manager._load_channels_for_legion("legion-123")

    # Verify channel was loaded
    channel = await channel_manager.get_channel(channel_id)
    assert channel is not None
    assert channel.name == "test-channel"
    assert "minion-1" in channel.member_minion_ids


@pytest.mark.asyncio
async def test_load_channels_handles_missing_directory(channel_manager):
    """Test that loading from non-existent directory doesn't raise error."""
    # Should not raise error
    await channel_manager._load_channels_for_legion("non-existent-legion")


@pytest.mark.asyncio
async def test_load_channels_handles_corrupt_json(channel_manager, temp_data_dir):
    """Test that corrupt JSON files are skipped gracefully."""
    # Create valid channel first
    valid_channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="valid-channel",
        description="Test",
        purpose="coordination"
    )

    # Create corrupt channel file
    corrupt_channel_id = "corrupt-channel-123"
    corrupt_dir = temp_data_dir / "legions" / "legion-123" / "channels" / corrupt_channel_id
    corrupt_dir.mkdir(parents=True, exist_ok=True)

    corrupt_file = corrupt_dir / "channel_state.json"
    with open(corrupt_file, 'w', encoding='utf-8') as f:
        f.write("{ invalid json }")

    # Clear in-memory channels
    channel_manager.system.legion_coordinator.channels.clear()

    # Load channels - should skip corrupt file
    await channel_manager._load_channels_for_legion("legion-123")

    # Verify valid channel was loaded
    valid_channel = await channel_manager.get_channel(valid_channel_id)
    assert valid_channel is not None

    # Verify corrupt channel was not loaded
    corrupt_channel = await channel_manager.get_channel(corrupt_channel_id)
    assert corrupt_channel is None


# ==================== CHANNEL STATE SURVIVES RESTART TEST ====================

@pytest.mark.asyncio
async def test_channel_state_survives_restart(legion_system, temp_data_dir):
    """
    Test acceptance criteria: Channel state survives restart.

    Simulates server restart by:
    1. Creating channel with members
    2. Creating new LegionSystem (simulates restart)
    3. Loading channels
    4. Verifying channel state is restored
    """
    # Step 1: Create channel and persist
    channel_manager1 = legion_system.channel_manager
    channel_id = await channel_manager1.create_channel(
        legion_id="legion-123",
        name="persistent-channel",
        description="Channel that survives restart",
        purpose="coordination",
        member_minion_ids=["minion-1", "minion-2", "minion-3"]
    )

    original_channel = await channel_manager1.get_channel(channel_id)
    assert original_channel is not None

    # Step 2: Simulate restart by creating new LegionSystem
    mock_session_coordinator = Mock()
    mock_session_coordinator.data_dir = temp_data_dir

    # Create new system
    new_system = LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Step 3: Load channels
    channel_manager2 = new_system.channel_manager
    await channel_manager2._load_channels_for_legion("legion-123")

    # Step 4: Verify channel state was restored
    restored_channel = await channel_manager2.get_channel(channel_id)
    assert restored_channel is not None
    assert restored_channel.name == "persistent-channel"
    assert restored_channel.description == "Channel that survives restart"
    assert restored_channel.purpose == "coordination"
    assert len(restored_channel.member_minion_ids) == 3
    assert "minion-1" in restored_channel.member_minion_ids
    assert "minion-2" in restored_channel.member_minion_ids
    assert "minion-3" in restored_channel.member_minion_ids


# ==================== UPDATED_AT TIMESTAMP TESTS ====================

@pytest.mark.asyncio
async def test_add_member_updates_timestamp(channel_manager):
    """Test that adding member updates updated_at timestamp."""
    # Create channel
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1"]
    )

    original_channel = await channel_manager.get_channel(channel_id)
    original_updated_at = original_channel.updated_at

    # Wait a moment and add member
    import time
    time.sleep(0.01)
    await channel_manager.add_member(channel_id, "minion-2")

    # Verify timestamp was updated
    updated_channel = await channel_manager.get_channel(channel_id)
    assert updated_channel.updated_at > original_updated_at


@pytest.mark.asyncio
async def test_remove_member_updates_timestamp(channel_manager):
    """Test that removing member updates updated_at timestamp."""
    # Create channel
    channel_id = await channel_manager.create_channel(
        legion_id="legion-123",
        name="test-channel",
        description="Test",
        purpose="coordination",
        member_minion_ids=["minion-1", "minion-2"]
    )

    original_channel = await channel_manager.get_channel(channel_id)
    original_updated_at = original_channel.updated_at

    # Wait a moment and remove member
    import time
    time.sleep(0.01)
    await channel_manager.remove_member(channel_id, "minion-1")

    # Verify timestamp was updated
    updated_channel = await channel_manager.get_channel(channel_id)
    assert updated_channel.updated_at > original_updated_at
