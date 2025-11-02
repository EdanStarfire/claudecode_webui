"""Tests for session_manager module."""

import asyncio
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ..session_manager import SessionInfo, SessionManager, SessionState


@pytest.fixture
async def temp_session_manager():
    """Create a temporary session manager for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = SessionManager(Path(temp_dir))
        await manager.initialize()
        yield manager


@pytest.fixture
def sample_session_config():
    """Sample session configuration for testing."""
    return {
        "working_directory": "/test/project",
        "permission_mode": "acceptEdits",
        "system_prompt": "Test system prompt",
        "tools": ["bash", "edit", "read"],
        "model": "claude-3-sonnet-20241022"
    }


class TestSessionInfo:
    """Test SessionInfo dataclass."""

    def test_session_info_creation(self):
        """Test SessionInfo creation and default values."""
        session_id = "test-session-123"
        now = datetime.now(UTC)

        info = SessionInfo(
            session_id=session_id,
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now
        )

        assert info.session_id == session_id
        assert info.state == SessionState.CREATED
        assert info.created_at == now
        assert info.updated_at == now
        assert info.working_directory is None
        assert info.current_permission_mode == "acceptEdits"
        assert info.tools == ["bash", "edit", "read"]
        assert info.model is None  # No default model

    def test_session_info_to_dict(self):
        """Test SessionInfo to_dict conversion."""
        session_id = "test-session-123"
        now = datetime.now(UTC)

        info = SessionInfo(
            session_id=session_id,
            state=SessionState.ACTIVE,
            created_at=now,
            updated_at=now,
            working_directory="/test/path"
        )

        data = info.to_dict()

        assert data["session_id"] == session_id
        assert data["state"] == "active"
        assert data["created_at"] == now.isoformat()
        assert data["updated_at"] == now.isoformat()
        assert data["working_directory"] == "/test/path"

    def test_session_info_from_dict(self):
        """Test SessionInfo from_dict creation."""
        session_id = "test-session-123"
        now = datetime.now(UTC)

        data = {
            "session_id": session_id,
            "state": "active",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "working_directory": "/test/path",
            "current_permission_mode": "acceptEdits",
            "system_prompt": None,
            "tools": ["bash", "edit"],
            "model": "claude-3-sonnet-20241022",
            "error_message": None
        }

        info = SessionInfo.from_dict(data)

        assert info.session_id == session_id
        assert info.state == SessionState.ACTIVE
        assert info.created_at == now
        assert info.updated_at == now
        assert info.working_directory == "/test/path"
        assert info.tools == ["bash", "edit"]


class TestSessionManager:
    """Test SessionManager functionality."""

    @pytest.mark.asyncio
    async def test_initialization(self, temp_session_manager):
        """Test session manager initialization."""
        manager = temp_session_manager

        # Check that data directories were created
        assert manager.data_dir.exists()
        assert manager.sessions_dir.exists()
        assert isinstance(manager._active_sessions, dict)
        assert isinstance(manager._session_locks, dict)

    @pytest.mark.asyncio
    async def test_create_session_basic(self, temp_session_manager, sample_session_config):
        """Test basic session creation."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)

        assert session_id is not None
        assert len(session_id) > 0
        assert session_id in manager._active_sessions

        session_info = manager._active_sessions[session_id]
        assert session_info.session_id == session_id
        assert session_info.state == SessionState.CREATED
        assert session_info.working_directory == sample_session_config["working_directory"]
        assert session_info.current_permission_mode == sample_session_config["permission_mode"]

        # Check that session directory and state file were created
        session_dir = manager.sessions_dir / session_id
        assert session_dir.exists()

        state_file = session_dir / "state.json"
        assert state_file.exists()

    @pytest.mark.asyncio
    async def test_create_session_with_defaults(self, temp_session_manager):
        """Test session creation with default values."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id)

        session_info = manager._active_sessions[session_id]
        assert session_info.current_permission_mode == "acceptEdits"
        assert session_info.tools == []  # No default tools (must be specified explicitly)
        assert session_info.model is None  # No default model

    @pytest.mark.asyncio
    async def test_start_session(self, temp_session_manager, sample_session_config):
        """Test session start functionality."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        success = await manager.start_session(session_id)

        assert success is True

        session_info = manager._active_sessions[session_id]
        # State is STARTING immediately after start_session() - transitions to ACTIVE when SDK actually starts
        assert session_info.state == SessionState.STARTING

    @pytest.mark.asyncio
    async def test_start_nonexistent_session(self, temp_session_manager):
        """Test starting a non-existent session."""
        manager = temp_session_manager

        success = await manager.start_session("nonexistent-session")
        assert success is False

    @pytest.mark.asyncio
    async def test_pause_session(self, temp_session_manager, sample_session_config):
        """Test session pause functionality."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        await manager.start_session(session_id)

        # Manually transition to ACTIVE state to simulate SDK fully started
        await manager._update_session_state(session_id, SessionState.ACTIVE)

        success = await manager.pause_session(session_id)

        assert success is True

        session_info = manager._active_sessions[session_id]
        assert session_info.state == SessionState.PAUSED

    @pytest.mark.asyncio
    async def test_pause_invalid_state(self, temp_session_manager, sample_session_config):
        """Test pausing session in invalid state."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        # Try to pause without starting
        success = await manager.pause_session(session_id)

        assert success is False

    @pytest.mark.asyncio
    async def test_terminate_session(self, temp_session_manager, sample_session_config):
        """Test session termination."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        await manager.start_session(session_id)
        success = await manager.terminate_session(session_id)

        assert success is True

        session_info = manager._active_sessions[session_id]
        assert session_info.state == SessionState.TERMINATED

    @pytest.mark.asyncio
    async def test_get_session_info(self, temp_session_manager, sample_session_config):
        """Test getting session information."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        session_info = await manager.get_session_info(session_id)

        assert session_info is not None
        assert session_info.session_id == session_id
        assert session_info.state == SessionState.CREATED

    @pytest.mark.asyncio
    async def test_get_nonexistent_session_info(self, temp_session_manager):
        """Test getting info for non-existent session."""
        manager = temp_session_manager

        session_info = await manager.get_session_info("nonexistent")
        assert session_info is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, temp_session_manager, sample_session_config):
        """Test listing all sessions."""
        manager = temp_session_manager

        # Create multiple sessions
        session_id_1 = str(uuid.uuid4())
        await manager.create_session(session_id_1, **sample_session_config)
        session_id_2 = str(uuid.uuid4())
        await manager.create_session(session_id_2, **sample_session_config)

        sessions = await manager.list_sessions()

        assert len(sessions) == 2
        session_ids = [s.session_id for s in sessions]
        assert session_id_1 in session_ids
        assert session_id_2 in session_ids

    @pytest.mark.asyncio
    async def test_get_session_directory(self, temp_session_manager, sample_session_config):
        """Test getting session directory path."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        session_dir = await manager.get_session_directory(session_id)

        assert session_dir is not None
        assert session_dir == manager.sessions_dir / session_id
        assert session_dir.exists()

    @pytest.mark.asyncio
    async def test_persistence_across_restarts(self, sample_session_config):
        """Test that sessions persist across manager restarts."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create first manager and session
            manager1 = SessionManager(temp_path)
            await manager1.initialize()

            session_id = str(uuid.uuid4())
            await manager1.create_session(session_id, **sample_session_config)
            await manager1.start_session(session_id)

            # Create second manager (simulating restart)
            manager2 = SessionManager(temp_path)
            await manager2.initialize()

            # Check that session was loaded
            assert session_id in manager2._active_sessions
            session_info = manager2._active_sessions[session_id]
            assert session_info.session_id == session_id
            assert session_info.working_directory == sample_session_config["working_directory"]

    @pytest.mark.asyncio
    async def test_concurrent_session_operations(self, temp_session_manager, sample_session_config):
        """Test concurrent session operations."""
        manager = temp_session_manager

        # Create session
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)

        # Run concurrent operations
        tasks = [
            manager.start_session(session_id),
            manager.get_session_info(session_id),
            manager.list_sessions()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check that no exceptions occurred
        for result in results:
            assert not isinstance(result, Exception)

    @pytest.mark.asyncio
    async def test_invalid_state_transitions(self, temp_session_manager, sample_session_config):
        """Test invalid state transitions are handled properly."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)

        # Try to pause without starting
        success = await manager.pause_session(session_id)
        assert success is False

        session_info = manager._active_sessions[session_id]
        assert session_info.state == SessionState.CREATED  # State unchanged

    @pytest.mark.asyncio
    async def test_session_state_persistence(self, temp_session_manager, sample_session_config):
        """Test that session state changes are persisted."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, **sample_session_config)
        session_dir = manager.sessions_dir / session_id
        state_file = session_dir / "state.json"

        # Start session and check persistence
        await manager.start_session(session_id)

        with open(state_file) as f:
            state_data = json.load(f)

        # State is persisted as 'starting' immediately after start_session()
        assert state_data["state"] == "starting"
