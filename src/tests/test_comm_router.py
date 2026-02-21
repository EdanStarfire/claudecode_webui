"""
Tests for CommRouter communication routing.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.legion_system import LegionSystem
from src.models.legion_models import Comm, CommType


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    from src.session_manager import SessionInfo, SessionState

    # Create a default active minion for tests (issue #349: is_minion removed)
    default_minion = Mock(spec=SessionInfo)
    default_minion.session_id = "test-minion-123"
    default_minion.name = "TestMinion"
    default_minion.project_id = "test-legion-456"
    default_minion.state = SessionState.ACTIVE

    mock_session_coordinator = Mock()
    mock_session_coordinator.send_message = AsyncMock()
    mock_session_coordinator.session_manager = Mock()
    mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=default_minion)
    mock_session_coordinator.start_session = AsyncMock()
    mock_session_coordinator.data_dir = Path("/tmp/test")

    system = LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Mock async methods that tests will use
    system.legion_coordinator.get_minion_info = AsyncMock(return_value=default_minion)

    return system


@pytest.fixture
def comm_router(legion_system):
    """Create CommRouter instance."""
    return legion_system.comm_router


@pytest.fixture
def sample_minion():
    """Create a sample minion SessionInfo (issue #349: is_minion removed)."""
    from src.session_manager import SessionInfo, SessionState
    mock = Mock(spec=SessionInfo)
    mock.session_id = "test-minion-123"
    mock.name = "TestMinion"
    mock.project_id = "test-legion-456"
    mock.state = SessionState.ACTIVE
    return mock


class TestCommRouter:
    """Test cases for CommRouter class."""

    def test_comm_router_initialization(self, comm_router):
        """Test CommRouter initializes with system reference."""
        assert comm_router is not None
        assert hasattr(comm_router, 'system')

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
    async def test_append_to_comm_log(self, comm_router, tmp_path):
        """Test appending Comm to JSONL log file."""
        # Point data_dir to tmp_path so _append_to_comm_log writes there
        comm_router.system.session_coordinator.data_dir = tmp_path

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="Test message"
        )

        await comm_router._append_to_comm_log(
            "test-legion-456",
            "minions/test-minion-123",
            comm
        )

        # Verify log file was created with comm data
        log_file = tmp_path / "legions" / "test-legion-456" / "minions" / "test-minion-123" / "comms.jsonl"
        assert log_file.exists()
        assert log_file.read_text().strip() != ""

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
