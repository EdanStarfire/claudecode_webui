"""
Tests for ArchiveManager - minion session archival before disposal.
"""

import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from backend.legion.archive_manager import ArchiveManager
from backend.message_migration_service import MessageMigrationService
from backend.models.archive_models import ArchiveResult, DisposalMetadata
from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionInfo, SessionState

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
    # Issue #2084 (stage 3-D-prep, §2): mirrors a real SessionCoordinator before
    # set_message_migration_service() has ever been called — these tests don't
    # exercise migrate-at-disposal, so archive_minion() must skip it cleanly.
    system.session_coordinator.message_migration_service = None

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


class TestArchiveManagerErasure:
    """Tests for erase_history, erase_archives, and check_history_archives_exist."""

    @pytest.mark.asyncio
    async def test_issue_691_erase_history(self, mock_system):
        """Erase history clears history/ directory."""
        system, temp_path = mock_system
        session_id = "test-session-1"

        # Create history files
        history_dir = temp_path / "sessions" / session_id / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "20240101_120000.md").write_text("# History")

        manager = ArchiveManager(system)
        result = await manager.erase_history(session_id)

        assert result is True
        assert not history_dir.exists()

    @pytest.mark.asyncio
    async def test_issue_691_erase_history_no_history(self, mock_system):
        """Erase history returns False when no history exists."""
        system, temp_path = mock_system
        manager = ArchiveManager(system)
        result = await manager.erase_history("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_issue_691_erase_archives(self, mock_system):
        """Erase archives clears archives/{session_id}/ directory."""
        system, temp_path = mock_system
        session_id = "test-session-1"

        manager = ArchiveManager(system)
        # Create archive directory
        archive_dir = manager.archives_dir / session_id / "20240101_120000"
        archive_dir.mkdir(parents=True)
        (archive_dir / "messages.jsonl").write_text('{"test": true}\n')

        result = await manager.erase_archives(session_id)

        assert result is True
        assert not (manager.archives_dir / session_id).exists()

    @pytest.mark.asyncio
    async def test_issue_691_erase_archives_no_archives(self, mock_system):
        """Erase archives returns False when no archives exist."""
        system, temp_path = mock_system
        manager = ArchiveManager(system)
        result = await manager.erase_archives("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_issue_691_erase_history_preserves_archives(self, mock_system):
        """Erasing history does not affect archives."""
        system, temp_path = mock_system
        session_id = "test-session-1"

        # Create both history and archives
        history_dir = temp_path / "sessions" / session_id / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "test.md").write_text("# History")

        manager = ArchiveManager(system)
        archive_dir = manager.archives_dir / session_id / "20240101_120000"
        archive_dir.mkdir(parents=True)
        (archive_dir / "messages.jsonl").write_text('{"test": true}\n')

        await manager.erase_history(session_id)

        assert not history_dir.exists()
        assert archive_dir.exists()

    @pytest.mark.asyncio
    async def test_issue_691_erase_archives_preserves_history(self, mock_system):
        """Erasing archives does not affect history."""
        system, temp_path = mock_system
        session_id = "test-session-1"

        # Create both
        history_dir = temp_path / "sessions" / session_id / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "test.md").write_text("# History")

        manager = ArchiveManager(system)
        archive_dir = manager.archives_dir / session_id / "20240101_120000"
        archive_dir.mkdir(parents=True)
        (archive_dir / "messages.jsonl").write_text('{"test": true}\n')

        await manager.erase_archives(session_id)

        assert history_dir.exists()
        assert not (manager.archives_dir / session_id).exists()

    @pytest.mark.asyncio
    async def test_issue_691_check_status_both_exist(self, mock_system):
        """Status check returns True for both when both exist."""
        system, temp_path = mock_system
        session_id = "test-session-1"

        # Create history
        history_dir = temp_path / "sessions" / session_id / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "test.md").write_text("# History")

        # Create archives
        manager = ArchiveManager(system)
        archive_dir = manager.archives_dir / session_id / "20240101_120000"
        archive_dir.mkdir(parents=True)

        status = await manager.check_history_archives_exist(session_id)
        assert status["has_history"] is True
        assert status["has_archives"] is True

    @pytest.mark.asyncio
    async def test_issue_691_check_status_neither_exist(self, mock_system):
        """Status check returns False for both when neither exists."""
        system, temp_path = mock_system
        manager = ArchiveManager(system)
        status = await manager.check_history_archives_exist("nonexistent")
        assert status["has_history"] is False
        assert status["has_archives"] is False

    @pytest.mark.asyncio
    async def test_issue_691_check_status_empty_history_dir(self, mock_system):
        """Empty history dir (no .md files) returns has_history=False."""
        system, temp_path = mock_system
        session_id = "test-session-1"

        history_dir = temp_path / "sessions" / session_id / "history"
        history_dir.mkdir(parents=True)
        # Dir exists but no .md files

        manager = ArchiveManager(system)
        status = await manager.check_history_archives_exist(session_id)
        assert status["has_history"] is False


class TestArchiveManagerInLegionSystem:
    """Test ArchiveManager integration with LegionSystem."""

    def test_archive_manager_initialized_in_legion_system(self):
        """Test that LegionSystem initializes ArchiveManager."""
        from backend.legion_system import LegionSystem

        system = LegionSystem(
            session_coordinator=Mock(),
            data_storage_manager=Mock(),
            template_manager=Mock()
        )

        assert system.archive_manager is not None
        assert system.archive_manager.system == system


class _StubSystem:
    """Minimal stand-in for LegionSystem exposing only what ArchiveManager touches,
    wired to a real SessionCoordinator (not a Mock) so migrate-at-disposal (§2)
    exercises the actual migration pipeline rather than asserting a mock was called."""

    def __init__(self, coordinator: SessionCoordinator):
        self.session_coordinator = coordinator


class TestArchiveMinionMigratesAtDisposal:
    """Issue #2084 (stage 3-D-prep, §2): archive_minion() must migrate a still-
    legacy live session to canonical shape before snapshotting, so every NEW
    archive is canonical from the moment it's created."""

    @pytest.fixture
    async def real_coordinator(self, tmp_path):
        coordinator = SessionCoordinator(tmp_path)
        await coordinator.initialize()
        service = MessageMigrationService(coordinator, coordinator.session_manager)
        coordinator.set_message_migration_service(service)
        yield coordinator
        await coordinator.cleanup()

    @pytest.fixture
    async def legacy_minion(self, real_coordinator):
        project = await real_coordinator.project_manager.create_project(
            name="Test Project", working_directory="/test/project"
        )
        session_id = str(uuid.uuid4())
        await real_coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(
                permission_mode="acceptEdits",
                system_prompt="Test system prompt",
                allowed_tools=["bash", "edit", "read"],
                model="claude-3-sonnet-20241022",
            ),
        )
        real_coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = real_coordinator._storage_managers[session_id]
        storage.messages_file.write_text(
            (FIXTURES_DIR / "tool_use" / "messages.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return session_id

    @pytest.mark.asyncio
    async def test_archive_is_canonical_after_disposal(self, real_coordinator, legacy_minion):
        system = _StubSystem(real_coordinator)
        manager = ArchiveManager(system)

        result = await manager.archive_minion(
            legacy_minion,
            reason="parent_initiated",
            parent_overseer_id=None,
            parent_overseer_name=None,
        )

        assert result.success is True
        archive_dir = Path(result.archive_path)
        state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert state["message_schema_version"] == CURRENT_MESSAGE_SCHEMA_VERSION
        assert state["message_migration_status"]["state"] == "completed"
        assert (archive_dir / "messages.jsonl").read_text(encoding="utf-8").strip() != ""

        # The live session itself is left canonical too (migrate_one() mutates
        # the live SessionManager, not a disposable copy).
        info = await real_coordinator.session_manager.get_session_info(legacy_minion)
        assert info.message_schema_version == CURRENT_MESSAGE_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_disposal_succeeds_when_migration_quarantines(
        self, real_coordinator, legacy_minion
    ):
        """A genuinely corrupt session must not block disposal — the archive
        simply inherits the live session's quarantined status, to be picked up
        by the backfill tool (§3) later like any other pre-existing legacy archive."""
        storage = real_coordinator._storage_managers[legacy_minion]
        storage.messages_file.write_text(
            storage.messages_file.read_text(encoding="utf-8") + "NOT VALID JSON\n",
            encoding="utf-8",
        )
        system = _StubSystem(real_coordinator)
        manager = ArchiveManager(system)

        result = await manager.archive_minion(
            legacy_minion,
            reason="parent_initiated",
            parent_overseer_id=None,
            parent_overseer_name=None,
        )

        assert result.success is True
        archive_dir = Path(result.archive_path)
        state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert state["message_migration_status"]["state"] == "quarantined"
        assert state["message_schema_version"] == 0

    @pytest.mark.asyncio
    async def test_already_canonical_session_skips_migration(
        self, real_coordinator
    ):
        """A session already at CURRENT_MESSAGE_SCHEMA_VERSION must not be
        re-migrated at disposal time."""
        project = await real_coordinator.project_manager.create_project(
            name="Test Project 2", working_directory="/test/project2"
        )
        session_id = str(uuid.uuid4())
        await real_coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(
                permission_mode="acceptEdits",
                system_prompt="Test system prompt",
                allowed_tools=["bash"],
                model="claude-3-sonnet-20241022",
            ),
        )
        system = _StubSystem(real_coordinator)
        manager = ArchiveManager(system)

        result = await manager.archive_minion(
            session_id,
            reason="parent_initiated",
            parent_overseer_id=None,
            parent_overseer_name=None,
        )

        assert result.success is True
        archive_dir = Path(result.archive_path)
        state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert state["message_schema_version"] == CURRENT_MESSAGE_SCHEMA_VERSION
        assert state["message_migration_status"] is None


class TestArchiveSessionForResetMigratesAtReset:
    """Issue #2091: _archive_session_for_reset() must migrate a still-legacy live
    session to canonical shape before snapshotting too — the same migrate-at-
    disposal fix as TestArchiveMinionMigratesAtDisposal above, applied to the
    second, separate archive-creation path used by session reset."""

    @pytest.fixture
    async def real_coordinator(self, tmp_path):
        coordinator = SessionCoordinator(tmp_path)
        await coordinator.initialize()
        service = MessageMigrationService(coordinator, coordinator.session_manager)
        coordinator.set_message_migration_service(service)
        yield coordinator
        await coordinator.cleanup()

    @pytest.fixture
    async def legacy_minion(self, real_coordinator):
        project = await real_coordinator.project_manager.create_project(
            name="Test Project", working_directory="/test/project"
        )
        session_id = str(uuid.uuid4())
        await real_coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(
                permission_mode="acceptEdits",
                system_prompt="Test system prompt",
                allowed_tools=["bash", "edit", "read"],
                model="claude-3-sonnet-20241022",
            ),
        )
        real_coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = real_coordinator._storage_managers[session_id]
        storage.messages_file.write_text(
            (FIXTURES_DIR / "tool_use" / "messages.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return session_id

    @staticmethod
    def _latest_archive_dir(coordinator: SessionCoordinator, session_id: str) -> Path:
        archives_root = coordinator.session_manager.data_dir / "archives" / "minions" / session_id
        return max(archives_root.iterdir(), key=lambda p: p.name)

    @pytest.mark.asyncio
    async def test_reset_archive_is_canonical(self, real_coordinator, legacy_minion):
        success = await real_coordinator._archive_session_for_reset(legacy_minion)

        assert success is True
        archive_dir = self._latest_archive_dir(real_coordinator, legacy_minion)
        state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert state["message_schema_version"] == CURRENT_MESSAGE_SCHEMA_VERSION
        assert state["message_migration_status"]["state"] == "completed"
        assert (archive_dir / "messages.jsonl").read_text(encoding="utf-8").strip() != ""

        # The live session itself is left canonical too (migrate_one() mutates
        # the live SessionManager, not a disposable copy).
        info = await real_coordinator.session_manager.get_session_info(legacy_minion)
        assert info.message_schema_version == CURRENT_MESSAGE_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_reset_succeeds_when_migration_quarantines(
        self, real_coordinator, legacy_minion
    ):
        """A genuinely corrupt session must not block the reset archive — it
        simply inherits the live session's quarantined status, same as disposal."""
        storage = real_coordinator._storage_managers[legacy_minion]
        storage.messages_file.write_text(
            storage.messages_file.read_text(encoding="utf-8") + "NOT VALID JSON\n",
            encoding="utf-8",
        )

        success = await real_coordinator._archive_session_for_reset(legacy_minion)

        assert success is True
        archive_dir = self._latest_archive_dir(real_coordinator, legacy_minion)
        state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert state["message_migration_status"]["state"] == "quarantined"
        assert state["message_schema_version"] == 0

    @pytest.mark.asyncio
    async def test_already_canonical_session_skips_migration(self, real_coordinator):
        """A session already at CURRENT_MESSAGE_SCHEMA_VERSION must not be
        re-migrated when archived for reset."""
        project = await real_coordinator.project_manager.create_project(
            name="Test Project 2", working_directory="/test/project2"
        )
        session_id = str(uuid.uuid4())
        await real_coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(
                permission_mode="acceptEdits",
                system_prompt="Test system prompt",
                allowed_tools=["bash"],
                model="claude-3-sonnet-20241022",
            ),
        )

        success = await real_coordinator._archive_session_for_reset(session_id)

        assert success is True
        archive_dir = self._latest_archive_dir(real_coordinator, session_id)
        state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert state["message_schema_version"] == CURRENT_MESSAGE_SCHEMA_VERSION
        assert state["message_migration_status"] is None
