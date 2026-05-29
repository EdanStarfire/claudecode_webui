"""Tests for session_manager module."""

import asyncio
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ..models.minion_template import MinionTemplate
from ..session_config import SessionConfig
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
    return SessionConfig(
        working_directory="/test/project",
        permission_mode="acceptEdits",
        system_prompt="Test system prompt",
        allowed_tools=["bash", "edit", "read"],
        model="claude-3-sonnet-20241022",
    )


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
        assert info.config == {}  # CONFIG_FIELDS live in config dict, not flat

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
            "error_message": None,
            "config": {"allowed_tools": ["bash", "edit"], "model": "claude-3-sonnet-20241022"},
        }

        info = SessionInfo.from_dict(data)

        assert info.session_id == session_id
        assert info.state == SessionState.ACTIVE
        assert info.created_at == now
        assert info.updated_at == now
        assert info.working_directory == "/test/path"
        assert info.config.get("allowed_tools") == ["bash", "edit"]

    def test_bare_mode_persists(self):
        """Test bare_mode=True round-trips through config → to_dict → from_dict."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-bare-123",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            config={"bare_mode": True},
        )
        restored = SessionInfo.from_dict(info.to_dict())
        assert restored.config.get("bare_mode") is True

    def test_bare_mode_default_false(self):
        """Test bare_mode absent from config dict defaults to None/False."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-bare-456",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        info = SessionInfo.from_dict(data)
        assert info.config.get("bare_mode", False) is False

    # --- Issue #1059: template_id / session_overrides schema tests ---

    def test_session_info_template_id_default(self):
        """template_id defaults to None when not provided."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-tmpl-default",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
        )
        assert info.template_id is None

    def test_session_info_config_default(self):
        """config defaults to {} when not provided."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-config-default",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
        )
        assert info.config == {}

    def test_session_info_to_dict_template_fields(self):
        """to_dict includes template_id and config."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-tmpl-dict",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            template_id="tmpl-1",
            config={"model": "opus"},
        )
        data = info.to_dict()
        assert data["template_id"] == "tmpl-1"
        assert data["config"]["model"] == "opus"

    def test_session_info_from_dict_backward_compat(self):
        """from_dict with missing template fields defaults to None / {}."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-backward-compat",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        info = SessionInfo.from_dict(data)
        assert info.template_id is None
        assert info.config == {}

    def test_session_info_from_dict_with_template_fields(self):
        """from_dict preserves explicit template_id and config."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-from-dict-tmpl",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "template_id": "tmpl-42",
            "config": {"effort": "high"},
        }
        info = SessionInfo.from_dict(data)
        assert info.template_id == "tmpl-42"
        assert info.config.get("effort") == "high"

    def test_session_info_round_trip_template_fields(self):
        """to_dict() → from_dict() round-trip preserves template_id and config."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-round-trip",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            template_id="tmpl-rt",
            config={"model": "haiku", "thinking_mode": "adaptive"},
        )
        restored = SessionInfo.from_dict(info.to_dict())
        assert restored.template_id == "tmpl-rt"
        assert restored.config == {"model": "haiku", "thinking_mode": "adaptive"}


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
        await manager.create_session(session_id, config=sample_session_config)

        assert session_id is not None
        assert len(session_id) > 0
        assert session_id in manager._active_sessions

        session_info = manager._active_sessions[session_id]
        assert session_info.session_id == session_id
        assert session_info.state == SessionState.CREATED
        assert session_info.working_directory == sample_session_config.working_directory
        assert session_info.current_permission_mode == sample_session_config.permission_mode

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
        await manager.create_session(session_id, config=SessionConfig())

        session_info = manager._active_sessions[session_id]
        assert session_info.current_permission_mode == "acceptEdits"
        # CONFIG_FIELDS live in session.config, not as flat attributes
        assert session_info.config.get("allowed_tools") is None
        assert session_info.config.get("model") is None

    @pytest.mark.asyncio
    async def test_start_session(self, temp_session_manager, sample_session_config):
        """Test session start functionality."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=sample_session_config)
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
        """Test session pause functionality (internal use for permission prompts)."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=sample_session_config)
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
        await manager.create_session(session_id, config=sample_session_config)
        # Try to pause without starting
        success = await manager.pause_session(session_id)

        assert success is False

    @pytest.mark.asyncio
    async def test_terminate_session(self, temp_session_manager, sample_session_config):
        """Test session termination."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=sample_session_config)
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
        await manager.create_session(session_id, config=sample_session_config)
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
        await manager.create_session(session_id_1, config=sample_session_config)
        session_id_2 = str(uuid.uuid4())
        await manager.create_session(session_id_2, config=sample_session_config)

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
        await manager.create_session(session_id, config=sample_session_config)
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
            await manager1.create_session(session_id, config=sample_session_config)
            await manager1.start_session(session_id)

            # Create second manager (simulating restart)
            manager2 = SessionManager(temp_path)
            await manager2.initialize()

            # Check that session was loaded
            assert session_id in manager2._active_sessions
            session_info = manager2._active_sessions[session_id]
            assert session_info.session_id == session_id
            assert session_info.working_directory == sample_session_config.working_directory

    @pytest.mark.asyncio
    async def test_concurrent_session_operations(self, temp_session_manager, sample_session_config):
        """Test concurrent session operations."""
        manager = temp_session_manager

        # Create session
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=sample_session_config)

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
        await manager.create_session(session_id, config=sample_session_config)

        # Try to pause without starting — should fail gracefully
        success = await manager.pause_session(session_id)
        assert success is False

        session_info = manager._active_sessions[session_id]
        assert session_info.state == SessionState.CREATED  # State unchanged

    @pytest.mark.asyncio
    async def test_session_state_persistence(self, temp_session_manager, sample_session_config):
        """Test that session state changes are persisted."""
        manager = temp_session_manager

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=sample_session_config)
        session_dir = manager.sessions_dir / session_id
        state_file = session_dir / "state.json"

        # Start session and check persistence
        await manager.start_session(session_id)

        with open(state_file) as f:
            state_data = json.load(f)

        # State is persisted as 'starting' immediately after start_session()
        assert state_data["state"] == "starting"


# --- Issue #1059 Phase 2 / Issue #1230: Config dict update tests ---


def _make_template(template_id: str = "tmpl-001", permission_mode: str = "acceptEdits", **kwargs) -> MinionTemplate:
    """Build a test MinionTemplate. CONFIG_FIELDS go into config dict."""
    from ..session_config import CONFIG_FIELDS as _CF
    config = {"permission_mode": permission_mode}
    config.update({k: v for k, v in kwargs.items() if k in _CF})
    identity = {k: v for k, v in kwargs.items() if k not in _CF}
    return MinionTemplate(template_id=template_id, name="Test Template", config=config, **identity)


def _make_template_manager(template: MinionTemplate | None) -> AsyncMock:
    tm = AsyncMock()
    tm.get_template = AsyncMock(return_value=template)
    return tm


@pytest.mark.asyncio
class TestConfigDictUpdates:
    """Tests for config dict write behavior in update_session/update_permission_mode (issue #1230)."""

    async def test_config_field_update_writes_to_config_dict(self, temp_session_manager):
        """update_session with a CONFIG_FIELD writes to session.config."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=SessionConfig())

        await manager.update_session(session_id, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        assert session.config.get("model") == "claude-opus-4-5"

    async def test_non_config_field_not_written_to_config(self, temp_session_manager):
        """update_session with a non-CONFIG_FIELD (e.g. queue_paused) does NOT write to config."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=SessionConfig())

        await manager.update_session(session_id, queue_paused=True)

        session = await manager.get_session_info(session_id)
        assert "queue_paused" not in session.config
        assert session.queue_paused is True

    async def test_update_permission_mode_writes_to_config_and_flat(self, temp_session_manager):
        """update_permission_mode writes to both config['permission_mode'] and current_permission_mode."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=SessionConfig())

        await manager.update_permission_mode(session_id, "bypassPermissions")

        session = await manager.get_session_info(session_id)
        assert session.config.get("permission_mode") == "bypassPermissions"
        assert session.current_permission_mode == "bypassPermissions"

    async def test_update_session_multiple_config_fields(self, temp_session_manager):
        """update_session writes multiple CONFIG_FIELDS to config dict."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=SessionConfig())

        await manager.update_session(session_id, model="claude-opus-4-5", sandbox_enabled=True)

        session = await manager.get_session_info(session_id)
        assert session.config.get("model") == "claude-opus-4-5"
        assert session.config.get("sandbox_enabled") is True


# --- Issue #1059 / #1230: Session config storage tests ---


@pytest.mark.asyncio
class TestSessionConfigStorage:
    """Tests for issue #1230 — CONFIG_FIELDS stored in session.config dict."""

    @pytest.fixture
    def config_with_template(self):
        return SessionConfig(
            working_directory="/test/project",
            permission_mode="acceptEdits",
            system_prompt="Stored in config dict",
            allowed_tools=["bash", "edit"],
            model="claude-opus-4-5",
            template_id="tmpl-test-001",
        )

    @pytest.fixture
    def config_without_template(self):
        return SessionConfig(
            working_directory="/test/project",
            permission_mode="acceptEdits",
            system_prompt="Stored in config dict",
            allowed_tools=["bash", "edit"],
            model="claude-opus-4-5",
        )

    async def test_config_fields_stored_in_config_dict(
        self, temp_session_manager, config_with_template
    ):
        """CONFIG_FIELDS from SessionConfig are stored in session.config dict (not flat)."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config_with_template)
        session = await manager.get_session_info(session_id)

        assert session.template_id == "tmpl-test-001"
        # CONFIG_FIELDS are in session.config
        assert session.config.get("model") == "claude-opus-4-5"
        assert session.config.get("system_prompt") == "Stored in config dict"
        assert session.config.get("allowed_tools") == ["bash", "edit"]

    async def test_session_state_fields_remain_flat(
        self, temp_session_manager, config_with_template
    ):
        """Non-CONFIG_FIELDS (runtime state) remain as flat attributes."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config_with_template)
        session = await manager.get_session_info(session_id)

        # Runtime-state fields stay flat
        assert session.current_permission_mode == "acceptEdits"
        assert session.initial_permission_mode == "acceptEdits"
        assert session.working_directory == "/test/project"
        assert session.template_id == "tmpl-test-001"

    async def test_config_fields_stored_without_template(
        self, temp_session_manager, config_without_template
    ):
        """Non-template sessions also store CONFIG_FIELDS in config dict."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config_without_template)
        session = await manager.get_session_info(session_id)

        assert session.template_id is None
        assert session.config.get("model") == "claude-opus-4-5"
        assert session.config.get("system_prompt") == "Stored in config dict"
        assert session.config.get("allowed_tools") == ["bash", "edit"]

    async def test_update_session_writes_config_field_to_config_dict(
        self, temp_session_manager
    ):
        """update_session() writes CONFIG_FIELDS into session.config."""
        manager = temp_session_manager
        config = SessionConfig(template_id="tmpl-x")
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config)

        await manager.update_session(session_id, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        assert session.config.get("model") == "claude-opus-4-5"

    async def test_update_session_non_config_field_stays_flat(
        self, temp_session_manager
    ):
        """update_session() for non-CONFIG_FIELD sets flat attribute, not config dict."""
        manager = temp_session_manager
        config = SessionConfig()
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config)

        await manager.update_session(session_id, queue_paused=True)

        session = await manager.get_session_info(session_id)
        assert session.queue_paused is True
        assert "queue_paused" not in session.config


# --- Issue #1230: Session migration tests ---


class TestMigrateSessionToConfigDict:
    """Test _migrate_session_to_config_dict — pre-1230 flat fields → config dict."""

    def test_template_linked_session_overrides_promoted_flat_fields_dropped(self):
        """Template-linked session: session_overrides promoted; flat CONFIG_FIELDS dropped."""
        from src.session_manager import _migrate_session_to_config_dict

        raw = {
            "session_id": "sess-001",
            "template_id": "tmpl-abc",
            "name": "Worker",
            "state": "active",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            # Stale flat CONFIG_FIELDS (should be dropped, not promoted)
            "permission_mode": "acceptEdits",
            "model": "claude-sonnet-4-20250514",
            # session_overrides are the real user intent (should be promoted)
            "session_overrides": {
                "model": "claude-opus-4-7",
                "bare_mode": True,
            },
        }

        migrated, changed = _migrate_session_to_config_dict(raw)

        assert changed is True
        config = migrated["config"]
        # session_overrides promoted
        assert config["model"] == "claude-opus-4-7"
        assert config["bare_mode"] is True
        # Stale flat fields NOT promoted (template-linked)
        assert "permission_mode" not in config
        # Flat keys removed from top level
        assert "permission_mode" not in migrated
        assert "session_overrides" not in migrated

    def test_non_template_session_non_default_flat_fields_promoted(self):
        """Non-template session: non-default flat CONFIG_FIELDS are promoted."""
        from src.session_manager import _migrate_session_to_config_dict

        raw = {
            "session_id": "sess-002",
            "name": "Standalone",
            "state": "active",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "permission_mode": "bypassPermissions",  # non-default — promote
            "docker_enabled": True,                 # non-default — promote
            "history_distillation_enabled": True,   # default — drop
        }

        migrated, changed = _migrate_session_to_config_dict(raw)

        assert changed is True
        config = migrated["config"]
        assert config["permission_mode"] == "bypassPermissions"
        assert config["docker_enabled"] is True
        # Default value dropped
        assert "history_distillation_enabled" not in config

    def test_idempotent_when_config_already_present(self):
        """Migration is a no-op when 'config' key already exists."""
        from src.session_manager import _migrate_session_to_config_dict

        raw = {
            "session_id": "sess-003",
            "config": {"permission_mode": "acceptEdits"},
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

        migrated, changed = _migrate_session_to_config_dict(raw)

        assert changed is False
        assert migrated is raw  # Same object returned unchanged

    @pytest.mark.asyncio
    async def test_orphan_key_in_config_does_not_fail_resolution(self):
        """Config dict with an unknown/removed field does not cause AttributeError at resolution.

        At resolution time, CONFIG_FIELDS filter silently drops orphan keys.
        """
        from src.config_resolution import resolve_effective_config

        now = datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        session = SessionInfo(
            session_id="sess-orphan",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            config={
                "permission_mode": "acceptEdits",
                "removed_field_from_future_version": "some_value",
            },
        )

        result = await resolve_effective_config(session, template_manager=None)
        assert result.permission_mode == "acceptEdits"
        # Orphan key not in effective config
        assert not hasattr(result, "removed_field_from_future_version")


# ---------------------------------------------------------------------------
# Issue #1530: upsert_link tests
# ---------------------------------------------------------------------------

class TestUpsertLink:
    @pytest.mark.asyncio
    async def test_issue_1530_upsert_link_appends_new(
        self, temp_session_manager, sample_session_config
    ):
        """A new label is appended to session.links."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        entry = await manager.upsert_link(sid, "GH Issue", "https://github.com/issues/1")
        assert entry["label"] == "GH Issue"
        assert entry["url"] == "https://github.com/issues/1"
        assert "registered_at" in entry

        session = await manager.get_session_info(sid)
        assert len(session.links) == 1
        assert session.links[0]["label"] == "GH Issue"

    @pytest.mark.asyncio
    async def test_issue_1530_upsert_link_updates_existing_label(
        self, temp_session_manager, sample_session_config
    ):
        """Re-registering the same label updates URL in-place (no duplicate)."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        await manager.upsert_link(sid, "Dashboard", "https://v1.example.com")
        await manager.upsert_link(sid, "Dashboard", "https://v2.example.com")

        session = await manager.get_session_info(sid)
        assert len(session.links) == 1
        assert session.links[0]["url"] == "https://v2.example.com"

    @pytest.mark.asyncio
    async def test_issue_1530_upsert_link_persists_across_reload(
        self, sample_session_config
    ):
        """Links written to state.json survive a SessionManager reload."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            manager1 = SessionManager(data_dir)
            await manager1.initialize()

            sid = str(uuid.uuid4())
            await manager1.create_session(sid, config=sample_session_config)
            await manager1.upsert_link(sid, "Docs", "https://docs.example.com")

            # Reload from disk
            manager2 = SessionManager(data_dir)
            await manager2.initialize()

            session = await manager2.get_session_info(sid)
            assert len(session.links) == 1
            assert session.links[0]["label"] == "Docs"
            assert session.links[0]["url"] == "https://docs.example.com"

    @pytest.mark.asyncio
    async def test_issue_1530_upsert_link_concurrent_no_dup(
        self, temp_session_manager, sample_session_config
    ):
        """Concurrent upserts of the same label produce exactly one entry (last writer wins)."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        # Run two concurrent upserts with same label but different URLs
        await asyncio.gather(
            manager.upsert_link(sid, "Race", "https://url-a.example.com"),
            manager.upsert_link(sid, "Race", "https://url-b.example.com"),
        )

        session = await manager.get_session_info(sid)
        assert len(session.links) == 1, "Concurrent upsert must not create duplicates"

    @pytest.mark.asyncio
    async def test_issue_1530_upsert_link_raises_for_missing_session(
        self, temp_session_manager
    ):
        """upsert_link raises ValueError if session_id does not exist."""
        manager = temp_session_manager
        with pytest.raises(ValueError):
            await manager.upsert_link("nonexistent-id", "X", "https://example.com")

    @pytest.mark.asyncio
    async def test_issue_1530_links_default_empty_on_new_session(
        self, temp_session_manager, sample_session_config
    ):
        """A freshly created session has an empty links list."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        session = await manager.get_session_info(sid)
        assert session.links == []

    def test_issue_1530_from_dict_backward_compat_no_links_key(self):
        """from_dict succeeds when the stored dict has no 'links' key (legacy data)."""
        now = datetime.now(UTC).isoformat()
        data = {
            "session_id": "abc",
            "state": "created",
            "created_at": now,
            "updated_at": now,
            "config": {},
        }
        info = SessionInfo.from_dict(data)
        assert info.links == []

    def test_issue_1530_to_dict_includes_links(self):
        """to_dict includes the links field."""
        now = datetime.now(UTC)
        links = [{"label": "X", "url": "https://x.example.com", "registered_at": now.isoformat()}]
        info = SessionInfo(
            session_id="abc",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            links=links,
        )
        d = info.to_dict()
        assert d["links"] == links


# ---------------------------------------------------------------------------
# Issue #1597: mark_unread tests
# ---------------------------------------------------------------------------


class TestMarkUnread:
    """Tests for SessionManager.mark_unread() (issue #1597)."""

    @pytest.mark.asyncio
    async def test_mark_unread_clears_last_viewed_at(
        self, temp_session_manager, sample_session_config
    ):
        """Happy path: session with completion+viewed → mark_unread clears last_viewed_at."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        # Record completion and viewed timestamps
        completion_ts = datetime.now(UTC)
        await manager.mark_completion(sid, completion_ts)
        await manager.mark_viewed(sid)

        session = manager._active_sessions.get(sid)
        assert session.last_viewed_at is not None, "Expected last_viewed_at set after mark_viewed"

        # Register a state-change callback to confirm broadcast fires
        state_changes = []

        async def _capture_change(session_id, new_state, is_processing, **kwargs):
            state_changes.append(session_id)

        manager.add_state_change_callback(_capture_change)

        result = await manager.mark_unread(sid)

        assert result is True
        session = manager._active_sessions.get(sid)
        assert session.last_viewed_at is None, "last_viewed_at must be None after mark_unread"

        # Confirm persisted to disk (reload and check)
        data_dir = manager.sessions_dir.parent
        manager2 = SessionManager(data_dir)
        await manager2.initialize()
        reloaded = await manager2.get_session_info(sid)
        assert reloaded.last_viewed_at is None, "last_viewed_at must persist as None"

        # State-change broadcast fired
        assert sid in state_changes, "mark_unread must broadcast state change"

    @pytest.mark.asyncio
    async def test_mark_unread_no_completion_returns_false(
        self, temp_session_manager, sample_session_config
    ):
        """Session without any completion: mark_unread returns False, no write."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        session = manager._active_sessions.get(sid)
        assert session.last_completion_at is None

        result = await manager.mark_unread(sid)

        assert result is False
        session = manager._active_sessions.get(sid)
        assert session.last_viewed_at is None  # unchanged (was already None)

    @pytest.mark.asyncio
    async def test_mark_unread_missing_session_returns_false(self, temp_session_manager):
        """Missing session: mark_unread returns False."""
        manager = temp_session_manager
        result = await manager.mark_unread("nonexistent-session-id")
        assert result is False
