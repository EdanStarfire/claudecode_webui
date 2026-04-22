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
        assert info.allowed_tools == ["bash", "edit", "read"]
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
            "allowed_tools": ["bash", "edit"],
            "model": "claude-3-sonnet-20241022",
            "error_message": None
        }

        info = SessionInfo.from_dict(data)

        assert info.session_id == session_id
        assert info.state == SessionState.ACTIVE
        assert info.created_at == now
        assert info.updated_at == now
        assert info.working_directory == "/test/path"
        assert info.allowed_tools == ["bash", "edit"]

    def test_bare_mode_persists(self):
        """Test bare_mode=True round-trips through to_dict/from_dict."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-bare-123",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            bare_mode=True,
        )
        restored = SessionInfo.from_dict(info.to_dict())
        assert restored.bare_mode is True

    def test_bare_mode_default_false(self):
        """Test bare_mode defaults to False when absent from dict."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-bare-456",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        info = SessionInfo.from_dict(data)
        assert info.bare_mode is False

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

    def test_session_info_session_overrides_default(self):
        """session_overrides defaults to {} via __post_init__ when not provided."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-overrides-default",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
        )
        assert info.session_overrides == {}

    def test_session_info_to_dict_template_fields(self):
        """to_dict includes template_id and session_overrides."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-tmpl-dict",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            template_id="tmpl-1",
            session_overrides={"model": "opus"},
        )
        data = info.to_dict()
        assert data["template_id"] == "tmpl-1"
        assert data["session_overrides"] == {"model": "opus"}

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
        assert info.session_overrides == {}

    def test_session_info_from_dict_with_template_fields(self):
        """from_dict preserves explicit template_id and session_overrides."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-from-dict-tmpl",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "template_id": "tmpl-42",
            "session_overrides": {"effort": "high"},
        }
        info = SessionInfo.from_dict(data)
        assert info.template_id == "tmpl-42"
        assert info.session_overrides == {"effort": "high"}

    def test_session_info_round_trip_template_fields(self):
        """to_dict() → from_dict() round-trip preserves template_id and session_overrides."""
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-round-trip",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            template_id="tmpl-rt",
            session_overrides={"model": "haiku", "thinking_mode": "adaptive"},
        )
        restored = SessionInfo.from_dict(info.to_dict())
        assert restored.template_id == "tmpl-rt"
        assert restored.session_overrides == {"model": "haiku", "thinking_mode": "adaptive"}


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
        assert session_info.allowed_tools == []  # No default tools (must be specified explicitly)
        assert session_info.model is None  # No default model

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


# --- Issue #1059 Phase 2: Override tracking tests ---


def _make_template(template_id: str = "tmpl-001", permission_mode: str = "acceptEdits", **kwargs) -> MinionTemplate:
    return MinionTemplate(template_id=template_id, name="Test Template", permission_mode=permission_mode, **kwargs)


def _make_template_manager(template: MinionTemplate | None) -> AsyncMock:
    tm = AsyncMock()
    tm.get_template = AsyncMock(return_value=template)
    return tm


@pytest.mark.asyncio
class TestOverrideTracking:
    """Tests for SessionManager._track_overrides() and its wiring into update_session/update_permission_mode."""

    async def _create_session_with_template(
        self,
        manager: SessionManager,
        template_id: str = "tmpl-001",
    ) -> str:
        """Helper: create a session linked to a template."""
        session_id = str(uuid.uuid4())
        config = SessionConfig(template_id=template_id)
        await manager.create_session(session_id, config=config)
        # Manually set template_id since create_session doesn't store it from SessionConfig yet
        await manager.update_session(session_id, template_id=template_id)
        return session_id

    async def test_override_add(self, temp_session_manager):
        """Updating a field to a value different from template records an override."""
        manager = temp_session_manager
        template = _make_template(model="claude-sonnet-4-6")
        tm = _make_template_manager(template)

        session_id = await self._create_session_with_template(manager)

        # Update model to something different from template
        await manager.update_session(session_id, template_manager=tm, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        assert session.session_overrides.get("model") == "claude-opus-4-5"

    async def test_override_remove(self, temp_session_manager):
        """Updating a field to match the template value removes the override."""
        manager = temp_session_manager
        template = _make_template(model="claude-sonnet-4-6")
        tm = _make_template_manager(template)

        session_id = await self._create_session_with_template(manager)
        # Seed an existing override
        await manager.update_session(session_id, template_manager=tm, model="claude-opus-4-5")
        session = await manager.get_session_info(session_id)
        assert "model" in session.session_overrides

        # Now update to match template value — override should disappear
        await manager.update_session(session_id, template_manager=tm, model="claude-sonnet-4-6")
        session = await manager.get_session_info(session_id)
        assert "model" not in session.session_overrides

    async def test_override_no_template(self, temp_session_manager):
        """Updating a field on a session without template_id leaves session_overrides unchanged."""
        manager = temp_session_manager
        tm = _make_template_manager(_make_template())

        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=SessionConfig())
        # Ensure no template_id
        session = await manager.get_session_info(session_id)
        assert session.template_id is None

        await manager.update_session(session_id, template_manager=tm, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        assert session.session_overrides == {}
        # get_template should not be called when template_id is None
        tm.get_template.assert_not_called()

    async def test_override_permission_mode(self, temp_session_manager):
        """update_permission_mode tracks override using canonical key 'permission_mode'."""
        manager = temp_session_manager
        template = _make_template(permission_mode="acceptEdits")
        tm = _make_template_manager(template)

        session_id = await self._create_session_with_template(manager)

        await manager.update_permission_mode(session_id, "bypassPermissions", template_manager=tm)

        session = await manager.get_session_info(session_id)
        # Override key is canonical "permission_mode", not "current_permission_mode"
        assert session.session_overrides.get("permission_mode") == "bypassPermissions"

    async def test_override_permission_mode_matches_template_removes_override(self, temp_session_manager):
        """Setting permission_mode to template value removes the override."""
        manager = temp_session_manager
        template = _make_template(permission_mode="acceptEdits")
        tm = _make_template_manager(template)

        session_id = await self._create_session_with_template(manager)

        # First set a non-template mode
        await manager.update_permission_mode(session_id, "bypassPermissions", template_manager=tm)
        session = await manager.get_session_info(session_id)
        assert "permission_mode" in session.session_overrides

        # Then set back to template mode
        await manager.update_permission_mode(session_id, "acceptEdits", template_manager=tm)
        session = await manager.get_session_info(session_id)
        assert "permission_mode" not in session.session_overrides

    async def test_override_template_deleted(self, temp_session_manager):
        """When template is deleted, _track_overrides exits silently with no change."""
        manager = temp_session_manager
        tm = _make_template_manager(None)  # Template not found

        session_id = await self._create_session_with_template(manager)

        # Should not raise; session_overrides unchanged
        await manager.update_session(session_id, template_manager=tm, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        assert session.session_overrides == {}

    async def test_non_config_field_not_tracked(self, temp_session_manager):
        """Fields not on MinionTemplate (e.g. queue_paused) don't add overrides."""
        manager = temp_session_manager
        template = _make_template()
        tm = _make_template_manager(template)

        session_id = await self._create_session_with_template(manager)

        await manager.update_session(session_id, template_manager=tm, queue_paused=True)

        session = await manager.get_session_info(session_id)
        assert "queue_paused" not in session.session_overrides


# --- Issue #1059: Lean session creation tests ---


@pytest.mark.asyncio
class TestLeanSessionCreation:
    """Tests for issue #1059 — template-linked sessions skip flat CONFIG_FIELDS."""

    @pytest.fixture
    def config_with_template(self):
        return SessionConfig(
            working_directory="/test/project",
            permission_mode="acceptEdits",
            system_prompt="Should not be stored flat",
            allowed_tools=["bash", "edit"],
            model="claude-opus-4-5",
            template_id="tmpl-test-001",
        )

    @pytest.fixture
    def config_without_template(self):
        return SessionConfig(
            working_directory="/test/project",
            permission_mode="acceptEdits",
            system_prompt="Stored flat for legacy sessions",
            allowed_tools=["bash", "edit"],
            model="claude-opus-4-5",
        )

    async def test_lean_session_skips_config_fields(
        self, temp_session_manager, config_with_template
    ):
        """Template-linked sessions must not store CONFIG_FIELDS flat on SessionInfo."""

        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config_with_template)
        session = await manager.get_session_info(session_id)

        assert session.template_id == "tmpl-test-001"
        # Core CONFIG_FIELDS must be at dataclass defaults, not copied from config
        assert session.model is None
        assert session.system_prompt is None
        assert session.allowed_tools == ["bash", "edit", "read"]  # __post_init__ default
        assert session.docker_enabled is False
        assert session.docker_proxy_allowlist_domains is None
        assert session.docker_proxy_credential_names is None
        assert session.mcp_server_ids == []  # __post_init__ default

    async def test_lean_session_keeps_session_state_fields(
        self, temp_session_manager, config_with_template
    ):
        """Template-linked sessions must still set session-state fields from config."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config_with_template)
        session = await manager.get_session_info(session_id)

        # Runtime-state fields are always set from config, even for template-linked sessions
        assert session.current_permission_mode == "acceptEdits"
        assert session.initial_permission_mode == "acceptEdits"
        assert session.working_directory == "/test/project"
        assert session.template_id == "tmpl-test-001"

    async def test_legacy_session_populates_flat_fields(
        self, temp_session_manager, config_without_template
    ):
        """Non-template sessions must still populate flat CONFIG_FIELDS (legacy path)."""
        manager = temp_session_manager
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config_without_template)
        session = await manager.get_session_info(session_id)

        assert session.template_id is None
        assert session.model == "claude-opus-4-5"
        assert session.system_prompt == "Stored flat for legacy sessions"
        assert session.allowed_tools == ["bash", "edit"]

    async def test_update_session_suppresses_config_fields_for_template_session(
        self, temp_session_manager
    ):
        """update_session() must NOT write CONFIG_FIELDS flat for template-linked sessions."""
        manager = temp_session_manager
        config = SessionConfig(template_id="tmpl-x")
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config)

        template = _make_template(model="claude-sonnet-4-6")
        tm = _make_template_manager(template)

        # Update model — for template session, flat field stays at default
        await manager.update_session(session_id, template_manager=tm, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        # Flat field must NOT be written
        assert session.model is None
        # Override must be recorded
        assert session.session_overrides.get("model") == "claude-opus-4-5"

    async def test_update_session_writes_flat_fields_for_legacy_session(
        self, temp_session_manager
    ):
        """update_session() still writes flat CONFIG_FIELDS for non-template sessions."""
        manager = temp_session_manager
        config = SessionConfig()  # No template_id
        session_id = str(uuid.uuid4())
        await manager.create_session(session_id, config=config)

        template = _make_template(model="claude-sonnet-4-6")
        tm = _make_template_manager(template)

        # Update model — for legacy session, flat field IS written
        await manager.update_session(session_id, template_manager=tm, model="claude-opus-4-5")

        session = await manager.get_session_info(session_id)
        assert session.model == "claude-opus-4-5"
        # No override tracking for legacy sessions
        assert session.session_overrides == {}
