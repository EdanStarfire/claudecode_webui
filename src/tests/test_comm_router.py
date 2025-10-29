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
from src.models.legion_models import Comm, CommType, InterruptPriority, Channel


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
    """Create a sample minion SessionInfo."""
    from src.session_manager import SessionInfo, SessionState
    mock = Mock(spec=SessionInfo)
    mock.session_id = "test-minion-123"
    mock.name = "TestMinion"
    mock.project_id = "test-legion-456"
    mock.is_minion = True
    mock.state = SessionState.ACTIVE
    return mock


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
    async def test_broadcast_to_channel_basic(self, comm_router):
        """Test basic channel broadcasting (legacy test - replaced by detailed tests below)."""
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
            from_minion_id=sample_minion.session_id,
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
            to_minion_id=sample_minion.session_id,
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


# ==================== CHANNEL BROADCASTING TESTS (Issue #112) ====================

class TestChannelBroadcasting:
    """Comprehensive tests for channel broadcasting functionality."""

    @pytest.fixture
    def mock_channel(self):
        """Create a mock channel with members."""
        return Channel(
            channel_id="channel-123",
            legion_id="legion-456",
            name="test-channel",
            description="Test channel for broadcasting",
            purpose="coordination",
            member_minion_ids=["minion-1", "minion-2", "minion-3", "minion-4"]
        )

    @pytest.fixture
    def mock_sender_session(self):
        """Create a mock sender minion session."""
        mock = Mock()
        mock.session_id = "minion-1"
        mock.name = "SenderMinion"
        mock.project_id = "legion-456"
        return mock

    @pytest.mark.asyncio
    async def test_broadcast_to_channel_with_members(self, legion_system, mock_channel, mock_sender_session):
        """Test broadcasting to channel with multiple members."""
        # Setup mocks
        legion_system.channel_manager.get_channel = AsyncMock(return_value=mock_channel)
        legion_system.comm_router._send_to_minion = AsyncMock(return_value=True)
        legion_system.comm_router._persist_comm = AsyncMock()

        # Create comm from minion-1 to channel
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-1",
            from_minion_name="SenderMinion",
            to_channel_id="channel-123",
            to_channel_name="test-channel",
            content="Hello channel members!",
            comm_type=CommType.INFO
        )

        # Broadcast
        result = await legion_system.comm_router._broadcast_to_channel(comm)

        # Verify success
        assert result is True

        # Verify _send_to_minion called for each recipient (excluding sender)
        assert legion_system.comm_router._send_to_minion.call_count == 3  # 4 members - 1 sender

    @pytest.mark.asyncio
    async def test_broadcast_excludes_sender(self, legion_system, mock_channel):
        """Test that sender does not receive their own broadcast."""
        # Setup mocks
        legion_system.channel_manager.get_channel = AsyncMock(return_value=mock_channel)

        # Track recipients
        recipients = []
        async def track_recipient(comm):
            recipients.append(comm.to_minion_id)
            return True

        legion_system.comm_router._send_to_minion = AsyncMock(side_effect=track_recipient)
        legion_system.comm_router._persist_comm = AsyncMock()

        # Create comm from minion-2 (a member)
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-2",
            from_minion_name="SenderMinion",
            to_channel_id="channel-123",
            to_channel_name="test-channel",
            content="Test message",
            comm_type=CommType.INFO
        )

        await legion_system.comm_router._broadcast_to_channel(comm)

        # Verify sender (minion-2) not in recipients
        assert "minion-2" not in recipients
        assert len(recipients) == 3
        assert "minion-1" in recipients
        assert "minion-3" in recipients
        assert "minion-4" in recipients

    @pytest.mark.asyncio
    async def test_broadcast_to_empty_channel(self, legion_system):
        """Test broadcasting to channel with no members (edge case)."""
        empty_channel = Channel(
            channel_id="empty-channel",
            legion_id="legion-456",
            name="empty-channel",
            description="Channel with no members",
            purpose="coordination",
            member_minion_ids=[]
        )

        legion_system.channel_manager.get_channel = AsyncMock(return_value=empty_channel)
        legion_system.comm_router._send_to_minion = AsyncMock(return_value=True)
        legion_system.comm_router._persist_comm = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_channel_id="empty-channel",
            to_channel_name="empty-channel",
            content="Test to empty channel",
            comm_type=CommType.INFO
        )

        # Should succeed (no-op, but not an error)
        result = await legion_system.comm_router._broadcast_to_channel(comm)
        assert result is True

        # _send_to_minion should not be called (no recipients)
        legion_system.comm_router._send_to_minion.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_to_sender_only_channel(self, legion_system):
        """Test broadcasting to channel where sender is the only member."""
        sender_only_channel = Channel(
            channel_id="sender-only",
            legion_id="legion-456",
            name="sender-only",
            description="Channel with only sender",
            purpose="coordination",
            member_minion_ids=["minion-1"]
        )

        legion_system.channel_manager.get_channel = AsyncMock(return_value=sender_only_channel)
        legion_system.comm_router._send_to_minion = AsyncMock(return_value=True)
        legion_system.comm_router._persist_comm = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-1",
            to_channel_id="sender-only",
            to_channel_name="sender-only",
            content="Test to self-only channel",
            comm_type=CommType.INFO
        )

        # Should succeed (no recipients after excluding sender)
        result = await legion_system.comm_router._broadcast_to_channel(comm)
        assert result is True

        # _send_to_minion should not be called (sender excluded)
        legion_system.comm_router._send_to_minion.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_to_nonexistent_channel(self, legion_system):
        """Test broadcasting to channel that doesn't exist."""
        legion_system.channel_manager.get_channel = AsyncMock(return_value=None)
        legion_system.comm_router._send_system_error_comm = AsyncMock()
        legion_system.comm_router._persist_comm = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-1",
            to_channel_id="nonexistent-channel",
            to_channel_name="nonexistent",
            content="Test to missing channel",
            comm_type=CommType.INFO
        )

        # Should return False (failure)
        result = await legion_system.comm_router._broadcast_to_channel(comm)
        assert result is False

        # Error comm should be sent to sender
        legion_system.comm_router._send_system_error_comm.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_preserves_channel_name(self, legion_system, mock_channel):
        """Test that channel name is preserved in recipient comms."""
        legion_system.channel_manager.get_channel = AsyncMock(return_value=mock_channel)

        # Track recipient comms
        recipient_comms = []
        async def track_comm(comm):
            recipient_comms.append(comm)
            return True

        legion_system.comm_router._send_to_minion = AsyncMock(side_effect=track_comm)
        legion_system.comm_router._persist_comm = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-1",
            to_channel_id="channel-123",
            to_channel_name="test-channel",
            content="Test message",
            comm_type=CommType.INFO
        )

        await legion_system.comm_router._broadcast_to_channel(comm)

        # Verify all recipient comms have channel name preserved
        for recipient_comm in recipient_comms:
            assert recipient_comm.to_channel_name == "test-channel"
            assert 'broadcast_from_channel' in recipient_comm.metadata
            assert recipient_comm.metadata['broadcast_from_channel'] == "channel-123"

    @pytest.mark.asyncio
    async def test_broadcast_partial_delivery_failure(self, legion_system, mock_channel):
        """Test handling of partial delivery failures."""
        legion_system.channel_manager.get_channel = AsyncMock(return_value=mock_channel)

        # Simulate partial failure
        delivery_attempts = []
        async def partial_failure(comm):
            delivery_attempts.append(comm.to_minion_id)
            # Fail for minion-3, succeed for others
            return comm.to_minion_id != "minion-3"

        legion_system.comm_router._send_to_minion = AsyncMock(side_effect=partial_failure)
        legion_system.comm_router._persist_comm = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-1",
            to_channel_id="channel-123",
            to_channel_name="test-channel",
            content="Test message",
            comm_type=CommType.INFO
        )

        # Should still return True (broadcast attempted)
        result = await legion_system.comm_router._broadcast_to_channel(comm)
        assert result is True

        # Verify attempts made for all recipients
        assert len(delivery_attempts) == 3  # minion-2, minion-3, minion-4


# ==================== MCP TOOL HANDLER TESTS ====================

class TestMCPChannelBroadcast:
    """Tests for send_comm_to_channel MCP tool handler."""

    @pytest.fixture
    def mock_legion_system_with_channel(self, legion_system):
        """Setup legion system with channel and minions."""
        # Mock channel
        mock_channel = Channel(
            channel_id="channel-123",
            legion_id="legion-456",
            name="api-team",
            description="API team channel",
            purpose="coordination",
            member_minion_ids=["minion-1", "minion-2", "minion-3"]
        )

        # Mock sender session
        mock_sender = Mock()
        mock_sender.session_id = "minion-1"
        mock_sender.name = "SenderMinion"
        mock_sender.project_id = "legion-456"

        legion_system.session_coordinator.session_manager.get_session_info = AsyncMock(return_value=mock_sender)
        legion_system.channel_manager.list_channels = AsyncMock(return_value=[mock_channel])
        legion_system.comm_router.route_comm = AsyncMock(return_value=True)

        return legion_system

    @pytest.mark.asyncio
    async def test_send_comm_to_channel_success(self, mock_legion_system_with_channel):
        """Test successful channel broadcast via MCP tool."""
        mcp_tools = mock_legion_system_with_channel.mcp_tools

        args = {
            "_from_minion_id": "minion-1",
            "channel_name": "api-team",
            "content": "Hello team!",
            "comm_type": "info"
        }

        result = await mcp_tools._handle_send_comm_to_channel(args)

        assert result["is_error"] is False
        assert "broadcast to channel 'api-team'" in result["content"][0]["text"]
        assert "2 recipient" in result["content"][0]["text"]  # 3 members - sender

    @pytest.mark.asyncio
    async def test_send_comm_to_channel_not_member(self, mock_legion_system_with_channel):
        """Test error when sender is not a member of channel."""
        mcp_tools = mock_legion_system_with_channel.mcp_tools

        args = {
            "_from_minion_id": "minion-999",  # Not a member
            "channel_name": "api-team",
            "content": "Test",
            "comm_type": "info"
        }

        # Update sender session to be non-member
        mock_sender = Mock()
        mock_sender.session_id = "minion-999"
        mock_sender.name = "NonMember"
        mock_sender.project_id = "legion-456"
        mock_legion_system_with_channel.session_coordinator.session_manager.get_session_info = AsyncMock(return_value=mock_sender)

        result = await mcp_tools._handle_send_comm_to_channel(args)

        assert result["is_error"] is True
        assert "not a member of channel" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_send_comm_to_channel_not_found(self, mock_legion_system_with_channel):
        """Test error when channel doesn't exist."""
        mcp_tools = mock_legion_system_with_channel.mcp_tools

        # Return empty list (no channels)
        mock_legion_system_with_channel.channel_manager.list_channels = AsyncMock(return_value=[])

        args = {
            "_from_minion_id": "minion-1",
            "channel_name": "nonexistent",
            "content": "Test",
            "comm_type": "info"
        }

        result = await mcp_tools._handle_send_comm_to_channel(args)

        assert result["is_error"] is True
        assert "Channel 'nonexistent' not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_send_comm_to_channel_missing_sender_id(self, mock_legion_system_with_channel):
        """Test error when sender ID is missing."""
        mcp_tools = mock_legion_system_with_channel.mcp_tools

        args = {
            "channel_name": "api-team",
            "content": "Test",
            # Missing _from_minion_id
        }

        result = await mcp_tools._handle_send_comm_to_channel(args)

        assert result["is_error"] is True
        assert "Unable to determine sender minion ID" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_send_comm_to_channel_invalid_comm_type(self, mock_legion_system_with_channel):
        """Test error with invalid comm_type."""
        mcp_tools = mock_legion_system_with_channel.mcp_tools

        args = {
            "_from_minion_id": "minion-1",
            "channel_name": "api-team",
            "content": "Test",
            "comm_type": "invalid"
        }

        result = await mcp_tools._handle_send_comm_to_channel(args)

        assert result["is_error"] is True
        assert "Invalid comm_type" in result["content"][0]["text"]
