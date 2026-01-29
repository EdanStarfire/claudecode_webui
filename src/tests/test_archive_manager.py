"""
Tests for ArchiveManager - minion session archival before disposal.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.legion.archive_manager import ArchiveManager
from src.models.archive_models import ArchiveResult, DisposalMetadata
from src.session_manager import SessionInfo, SessionState


@pytest.fixture
def mock_system():
    """Create a mock LegionSystem with necessary components."""
    system = Mock()

    # Create temp directory for tests
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Mock session_coordinator.session_manager
    system.session_coordinator = Mock()
    system.session_coordinator.session_manager = Mock()
    system.session_coordinator.session_manager.data_dir = temp_path
    system.session_coordinator.session_manager.sessions_dir = temp_path / "sessions"

    return system, temp_path


@pytest.fixture
def sample_session_info():
    """Create sample SessionInfo for testing."""
    from datetime import UTC, datetime

    # Issue #349: is_minion field removed - all sessions are minions
    return SessionInfo(
        session_id="test-minion-123",
        state=SessionState.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        name="TestMinion",
        role="Test Role",
        project_id="test-legion-456",
        overseer_level=1,
        child_minion_ids=["child-1", "child-2"]
    )


class TestDisposalMetadata:
    """Tests for DisposalMetadata dataclass."""

    def test_to_dict(self):
        """Test DisposalMetadata serialization."""
        metadata = DisposalMetadata(
            disposed_at=1706300000.0,
            reason="parent_initiated",
            parent_overseer_id="parent-123",
            parent_overseer_name="ParentMinion",
            legion_id="legion-456",
            final_state="active",
            minion_id="minion-789",
            minion_name="TestMinion",
            minion_role="Test Role",
            overseer_level=1,
            child_minion_ids=["child-1"],
            descendants_count=1
        )

        result = metadata.to_dict()

        assert result["disposed_at"] == 1706300000.0
        assert result["reason"] == "parent_initiated"
        assert result["parent_overseer_id"] == "parent-123"
        assert result["minion_name"] == "TestMinion"
        assert result["descendants_count"] == 1

    def test_from_dict(self):
        """Test DisposalMetadata deserialization."""
        data = {
            "disposed_at": 1706300000.0,
            "reason": "cascade_disposal",
            "parent_overseer_id": "parent-123",
            "parent_overseer_name": "ParentMinion",
            "legion_id": "legion-456",
            "final_state": "terminated",
            "minion_id": "minion-789",
            "minion_name": "ChildMinion",
            "minion_role": None,
            "overseer_level": 2,
            "child_minion_ids": [],
            "descendants_count": 0,
            "metadata": {}
        }

        metadata = DisposalMetadata.from_dict(data)

        assert metadata.reason == "cascade_disposal"
        assert metadata.minion_name == "ChildMinion"
        assert metadata.overseer_level == 2


class TestArchiveResult:
    """Tests for ArchiveResult dataclass."""

    def test_success_result(self):
        """Test successful ArchiveResult."""
        result = ArchiveResult(
            success=True,
            archive_path="/data/archives/minions/123/20240126_120000",
            minion_id="minion-123",
            minion_name="TestMinion",
            files_archived=["messages.jsonl", "state.json", "disposal_metadata.json"]
        )

        assert result.success is True
        assert result.archive_path is not None
        assert len(result.files_archived) == 3
        assert result.error_message is None

    def test_failure_result(self):
        """Test failed ArchiveResult."""
        result = ArchiveResult(
            success=False,
            archive_path=None,
            minion_id="minion-123",
            minion_name="TestMinion",
            error_message="Session not found"
        )

        assert result.success is False
        assert result.archive_path is None
        assert result.error_message == "Session not found"


class TestArchiveManager:
    """Tests for ArchiveManager class."""

    @pytest.mark.asyncio
    async def test_archive_minion_success(self, mock_system, sample_session_info):
        """Test successful minion archival."""
        system, temp_path = mock_system

        # Create session directory with test files
        session_dir = temp_path / "sessions" / sample_session_info.session_id
        session_dir.mkdir(parents=True)

        # Create test messages.jsonl
        messages_file = session_dir / "messages.jsonl"
        messages_file.write_text('{"type": "user", "content": "test"}\n')

        # Create test state.json
        state_file = session_dir / "state.json"
        state_file.write_text('{"state": "active"}')

        # Mock get_session_info
        system.session_coordinator.session_manager.get_session_info = AsyncMock(
            return_value=sample_session_info
        )

        # Create ArchiveManager and archive
        manager = ArchiveManager(system)
        result = await manager.archive_minion(
            minion_id=sample_session_info.session_id,
            reason="parent_initiated",
            parent_overseer_id="parent-123",
            parent_overseer_name="ParentMinion",
            descendants_count=0
        )

        # Verify success
        assert result.success is True
        assert result.archive_path is not None
        assert "messages.jsonl" in result.files_archived
        assert "state.json" in result.files_archived
        assert "disposal_metadata.json" in result.files_archived

        # Verify archive directory was created
        archive_path = Path(result.archive_path)
        assert archive_path.exists()

        # Verify files were copied
        assert (archive_path / "messages.jsonl").exists()
        assert (archive_path / "state.json").exists()

        # Verify disposal_metadata.json was created with correct content
        metadata_file = archive_path / "disposal_metadata.json"
        assert metadata_file.exists()
        with open(metadata_file) as f:
            metadata = json.load(f)
        assert metadata["reason"] == "parent_initiated"
        assert metadata["minion_name"] == "TestMinion"
        assert metadata["parent_overseer_id"] == "parent-123"

    @pytest.mark.asyncio
    async def test_archive_minion_session_not_found(self, mock_system):
        """Test archival when session doesn't exist."""
        system, temp_path = mock_system

        # Mock get_session_info returning None
        system.session_coordinator.session_manager.get_session_info = AsyncMock(
            return_value=None
        )

        manager = ArchiveManager(system)
        result = await manager.archive_minion(
            minion_id="nonexistent-123",
            reason="parent_initiated",
            parent_overseer_id="parent-123",
            parent_overseer_name="ParentMinion"
        )

        assert result.success is False
        assert result.error_message is not None
        assert "not found" in result.error_message

    @pytest.mark.asyncio
    async def test_archive_minion_missing_files(self, mock_system, sample_session_info):
        """Test archival when source files don't exist (still succeeds with metadata)."""
        system, temp_path = mock_system

        # Create empty session directory (no files)
        session_dir = temp_path / "sessions" / sample_session_info.session_id
        session_dir.mkdir(parents=True)

        system.session_coordinator.session_manager.get_session_info = AsyncMock(
            return_value=sample_session_info
        )

        manager = ArchiveManager(system)
        result = await manager.archive_minion(
            minion_id=sample_session_info.session_id,
            reason="parent_initiated",
            parent_overseer_id="parent-123",
            parent_overseer_name="ParentMinion"
        )

        # Should still succeed - metadata is always created
        assert result.success is True
        assert "disposal_metadata.json" in result.files_archived
        # messages.jsonl and state.json not in files_archived since they didn't exist
        assert "messages.jsonl" not in result.files_archived
        assert "state.json" not in result.files_archived

    @pytest.mark.asyncio
    async def test_get_archive_info_no_archives(self, mock_system):
        """Test get_archive_info when no archives exist."""
        system, temp_path = mock_system

        manager = ArchiveManager(system)
        archives = await manager.get_archive_info("nonexistent-minion")

        assert archives == []

    @pytest.mark.asyncio
    async def test_get_archive_info_with_archives(self, mock_system, sample_session_info):
        """Test get_archive_info returns archive list."""
        system, temp_path = mock_system

        # Create session directory and archive minion first
        session_dir = temp_path / "sessions" / sample_session_info.session_id
        session_dir.mkdir(parents=True)
        (session_dir / "messages.jsonl").write_text('{"test": true}\n')

        system.session_coordinator.session_manager.get_session_info = AsyncMock(
            return_value=sample_session_info
        )

        manager = ArchiveManager(system)

        # Archive the minion
        await manager.archive_minion(
            minion_id=sample_session_info.session_id,
            reason="test",
            parent_overseer_id="parent-123",
            parent_overseer_name="Parent"
        )

        # Get archive info
        archives = await manager.get_archive_info(sample_session_info.session_id)

        assert len(archives) == 1
        assert "timestamp" in archives[0]
        assert "path" in archives[0]
        assert archives[0]["metadata"]["reason"] == "test"

    @pytest.mark.asyncio
    async def test_archives_dir_property(self, mock_system):
        """Test archives_dir property creates directory."""
        system, temp_path = mock_system

        manager = ArchiveManager(system)
        archives_dir = manager.archives_dir

        assert archives_dir.exists()
        assert archives_dir == temp_path / "archives" / "minions"


class TestArchiveManagerInLegionSystem:
    """Test ArchiveManager integration with LegionSystem."""

    def test_archive_manager_initialized_in_legion_system(self):
        """Test that LegionSystem initializes ArchiveManager."""
        from src.legion_system import LegionSystem

        system = LegionSystem(
            session_coordinator=Mock(),
            data_storage_manager=Mock(),
            template_manager=Mock()
        )

        assert system.archive_manager is not None
        assert system.archive_manager.system == system
