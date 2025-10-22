"""
Tests for CommRouter communication routing.
"""

import pytest
import uuid
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.legion_system import LegionSystem
from src.legion.comm_router import CommRouter
from src.models.legion_models import Comm, CommType, InterruptPriority, MinionInfo, Channel


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    return LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock()
    )


@pytest.fixture
def comm_router(legion_system):
    """Create CommRouter instance."""
    return legion_system.comm_router


@pytest.fixture
def sample_minion():
    """Create a sample MinionInfo."""
    return MinionInfo(
        minion_id="test-minion-123",
        name="TestMinion",
        role="Test Role",
        legion_id="test-legion-456"
    )


@pytest.fixture
def sample_channel():
    """Create a sample Channel."""
    return Channel(
        channel_id="test-channel-789",
        legion_id="test-legion-456",
        name="test-channel",
        description="Test channel",
        purpose="coordination"
    )


class TestCommRouter:
    """Test cases for CommRouter class."""

    def test_comm_router_initialization(self, comm_router):
        """Test CommRouter initializes with system reference."""
        assert comm_router is not None
        assert hasattr(comm_router, 'system')

    def test_extract_tags_minion_names(self, comm_router):
        """Test extracting minion names from content."""
        content = "Hey #DatabaseExpert can you review this?"
        minion_names, channel_names = comm_router._extract_tags(content)

        assert "DatabaseExpert" in minion_names
        assert len(channel_names) == 0

    def test_extract_tags_channel_names(self, comm_router):
        """Test extracting channel names from content."""
        content = "Posted in #planning channel"
        minion_names, channel_names = comm_router._extract_tags(content)

        assert "planning" in channel_names
        assert len(minion_names) == 0

    def test_extract_tags_mixed(self, comm_router):
        """Test extracting both minion and channel tags."""
        content = "Check with #Alice and #Bob in #coordination"
        minion_names, channel_names = comm_router._extract_tags(content)

        assert "Alice" in minion_names
        assert "Bob" in minion_names
        assert "coordination" in channel_names

    def test_extract_tags_empty(self, comm_router):
        """Test extracting tags from content with no tags."""
        content = "This has no tags at all"
        minion_names, channel_names = comm_router._extract_tags(content)

        assert len(minion_names) == 0
        assert len(channel_names) == 0

    @pytest.mark.asyncio
    async def test_route_comm_validation(self, comm_router):
        """Test that route_comm validates Comm objects."""
        # Create invalid Comm (no destination)
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            content="Test message"
        )

        with pytest.raises(ValueError, match="exactly one destination"):
            await comm_router.route_comm(comm)

    @pytest.mark.asyncio
    async def test_send_to_minion(self, comm_router):
        """Test sending Comm to minion."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK
        )

        result = await comm_router._send_to_minion(comm)
        assert result is True

    @pytest.mark.asyncio
    async def test_broadcast_to_channel(self, comm_router):
        """Test broadcasting Comm to channel."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_channel_id="test-channel-789",
            content="Test broadcast",
            comm_type=CommType.REPORT
        )

        result = await comm_router._broadcast_to_channel(comm)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_to_user(self, comm_router):
        """Test sending Comm to user."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="test-minion-123",
            to_user=True,
            content="Test message to user",
            comm_type=CommType.REPORT
        )

        result = await comm_router._send_to_user(comm)
        assert result is True

    @pytest.mark.asyncio
    async def test_append_to_comm_log(self, comm_router):
        """Test appending Comm to JSONL log file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('src.legion.comm_router.Path', return_value=Path(temp_dir)):
                comm = Comm(
                    comm_id=str(uuid.uuid4()),
                    from_user=True,
                    to_minion_id="test-minion-123",
                    content="Test message"
                )

                # This will create the log file
                await comm_router._append_to_comm_log(
                    "test-legion-456",
                    "minions/test-minion-123",
                    comm
                )

                # Verify log file exists (in temp dir structure)
                # Note: Path is mocked, so this just tests the method runs without error

    @pytest.mark.asyncio
    async def test_persist_comm_from_minion(self, comm_router, sample_minion, legion_system):
        """Test persisting Comm from minion."""
        # Mock legion_coordinator.get_minion_info
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=sample_minion.minion_id,
            to_user=True,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_comm_log', new=AsyncMock()) as mock_append:
            await comm_router._persist_comm(comm)
            # Should append to source minion's log
            mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_comm_to_minion(self, comm_router, sample_minion, legion_system):
        """Test persisting Comm to minion."""
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id=sample_minion.minion_id,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_comm_log', new=AsyncMock()) as mock_append:
            await comm_router._persist_comm(comm)
            # Should append to destination minion's log
            mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_comm_to_channel(self, comm_router, sample_channel, legion_system):
        """Test persisting Comm to channel."""
        legion_system.channel_manager.get_channel = AsyncMock(return_value=sample_channel)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_channel_id=sample_channel.channel_id,
            content="Test broadcast"
        )

        with patch.object(comm_router, '_append_to_comm_log', new=AsyncMock()) as mock_append:
            await comm_router._persist_comm(comm)
            # Should append to channel's log
            mock_append.assert_called_once()
