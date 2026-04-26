"""
Tests for TemplateManager - template CRUD and MinionTemplate model.

Covers:
- MinionTemplate round-trip serialization (all fields)
- TemplateManager.create_template() with all parameters
- TemplateManager.update_template() with all parameters
- Field propagation: every MinionTemplate field must be accepted by create/update
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.models.minion_template import MinionTemplate
from src.session_config import SessionConfig
from src.session_manager import SessionInfo, SessionState
from src.template_manager import TemplateConflictError, TemplateInUseError, TemplateManager


@pytest.fixture
def data_dir():
    """Create a temporary data directory for template storage."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def manager(data_dir):
    """Create a TemplateManager with a temp directory."""
    return TemplateManager(data_dir)


# --- MinionTemplate model tests ---


class TestMinionTemplateRoundTrip:
    """Verify all fields survive to_dict() -> from_dict() round-trip."""

    def test_all_fields_round_trip(self):
        template = MinionTemplate(
            template_id="test-id",
            name="Test Template",
            permission_mode="default",
            allowed_tools=["bash", "read"],
            disallowed_tools=["write"],
            role="tester",
            system_prompt="You are a tester.",
            description="A test template",
            model="claude-sonnet-4-20250514",
            capabilities=["python", "testing"],
            override_system_prompt=True,
            sandbox_enabled=True,
            sandbox_config={"network": False},
            cli_path="/usr/bin/claude",
            docker_enabled=True,
            docker_image="claude-code:local",
            docker_extra_mounts=["/data:/data:ro"],
            thinking_mode="enabled",
            thinking_budget_tokens=5000,
            effort="high",
        )

        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)

        assert restored.template_id == "test-id"
        assert restored.name == "Test Template"
        assert restored.permission_mode == "default"
        assert restored.allowed_tools == ["bash", "read"]
        assert restored.disallowed_tools == ["write"]
        assert restored.role == "tester"
        assert restored.system_prompt == "You are a tester."
        assert restored.description == "A test template"
        assert restored.model == "claude-sonnet-4-20250514"
        assert restored.capabilities == ["python", "testing"]
        assert restored.override_system_prompt is True
        assert restored.sandbox_enabled is True
        assert restored.sandbox_config == {"network": False}
        assert restored.cli_path == "/usr/bin/claude"
        assert restored.docker_enabled is True
        assert restored.docker_image == "claude-code:local"
        assert restored.docker_extra_mounts == ["/data:/data:ro"]
        assert restored.thinking_mode == "enabled"
        assert restored.thinking_budget_tokens == 5000
        assert restored.effort == "high"

    def test_effort_max_normalizes_to_high(self):
        """Legacy effort='max' must be silently downgraded to 'high' on load."""
        template = MinionTemplate(
            template_id="legacy",
            name="Legacy Template",
            permission_mode="default",
        )
        data = template.to_dict()
        data['effort'] = 'max'  # Simulate legacy data on disk
        restored = MinionTemplate.from_dict(data)
        assert restored.effort == "high"

    def test_none_defaults_round_trip(self):
        """Fields with None defaults should survive round-trip."""
        template = MinionTemplate(
            template_id="minimal",
            name="Minimal",
            permission_mode="default",
        )

        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)

        assert restored.thinking_mode is None
        assert restored.thinking_budget_tokens is None
        assert restored.effort is None
        assert restored.cli_path is None
        assert restored.docker_enabled is False
        assert restored.docker_image is None


# --- TemplateManager.create_template() tests ---


class TestCreateTemplate:
    """Verify create_template accepts and persists all fields."""

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self, manager):
        template = await manager.create_template(
            name="Full Template",
            config=SessionConfig(
                permission_mode="acceptEdits",
                allowed_tools=["bash"],
                disallowed_tools=["write"],
                model="claude-sonnet-4-20250514",
                override_system_prompt=True,
                sandbox_enabled=True,
                sandbox_config={"network": False},
                cli_path="/usr/bin/claude",
                docker_enabled=True,
                docker_image="claude-code:local",
                docker_extra_mounts=["/data:/data:ro"],
                thinking_mode="enabled",
                thinking_budget_tokens=8000,
                effort="high",
            ),
            role="developer",
            system_prompt="You are a developer.",
            description="Full-featured template",
            capabilities=["python"],
        )

        assert template.name == "Full Template"
        assert template.thinking_mode == "enabled"
        assert template.thinking_budget_tokens == 8000
        assert template.effort == "high"
        assert template.docker_enabled is True
        assert template.cli_path == "/usr/bin/claude"

    @pytest.mark.asyncio
    async def test_create_persists_to_disk(self, manager):
        """Fields must survive create -> load cycle."""
        await manager.create_template(
            name="Persist Test",
            config=SessionConfig(
                permission_mode="default",
                thinking_mode="enabled",
                thinking_budget_tokens=4000,
                effort="medium",
            ),
        )

        # Create new manager pointing at same directory, reload from disk
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()

        templates = await manager2.list_templates()
        assert len(templates) == 1
        t = templates[0]
        assert t.name == "Persist Test"
        assert t.thinking_mode == "enabled"
        assert t.thinking_budget_tokens == 4000
        assert t.effort == "medium"

    @pytest.mark.asyncio
    async def test_create_minimal(self, manager):
        """Create with only required fields should not raise."""
        template = await manager.create_template(
            name="Minimal",
            config=SessionConfig(permission_mode="default"),
        )

        assert template.thinking_mode is None
        assert template.thinking_budget_tokens is None
        assert template.effort is None


# --- TemplateManager.update_template() tests ---


class TestUpdateTemplate:
    """Verify update_template accepts and persists all fields."""

    @pytest.mark.asyncio
    async def test_update_thinking_fields(self, manager):
        template = await manager.create_template(
            name="Update Test",
            config=SessionConfig(permission_mode="default"),
        )

        updated = await manager.update_template(
            template.template_id,
            thinking_mode="enabled",
            thinking_budget_tokens=10000,
            effort="high",
        )

        assert updated.thinking_mode == "enabled"
        assert updated.thinking_budget_tokens == 10000
        assert updated.effort == "high"

    @pytest.mark.asyncio
    async def test_update_persists_to_disk(self, manager):
        """Updated fields must survive reload from disk."""
        template = await manager.create_template(
            name="Update Persist",
            config=SessionConfig(permission_mode="default"),
        )

        await manager.update_template(
            template.template_id,
            thinking_mode="enabled",
            thinking_budget_tokens=6000,
            effort="low",
        )

        # Reload from disk
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()

        t = await manager2.get_template(template.template_id)
        assert t.thinking_mode == "enabled"
        assert t.thinking_budget_tokens == 6000
        assert t.effort == "low"

    @pytest.mark.asyncio
    async def test_update_all_fields(self, manager):
        """Every field accepted by create must also be accepted by update."""
        template = await manager.create_template(
            name="Update All",
            config=SessionConfig(permission_mode="default"),
        )

        updated = await manager.update_template(
            template.template_id,
            name="Updated All",
            permission_mode="acceptEdits",
            allowed_tools=["bash"],
            disallowed_tools=["write"],
            role="architect",
            system_prompt="Updated prompt.",
            description="Updated description",
            model="claude-sonnet-4-20250514",
            capabilities=["architecture"],
            override_system_prompt=True,
            sandbox_enabled=True,
            sandbox_config={"network": True},
            cli_path="/opt/claude",
            docker_enabled=True,
            docker_image="custom:latest",
            docker_extra_mounts=["/vol:/vol"],
            thinking_mode="enabled",
            thinking_budget_tokens=12000,
            effort="high",
        )

        assert updated.name == "Updated All"
        assert updated.thinking_mode == "enabled"
        assert updated.thinking_budget_tokens == 12000
        assert updated.effort == "high"
        assert updated.docker_enabled is True
        assert updated.cli_path == "/opt/claude"

    @pytest.mark.asyncio
    async def test_issue_1116_update_docker_proxy_fields(self, manager):
        """Regression #1116: docker proxy fields must persist via update_template()."""
        template = await manager.create_template(
            name="Proxy Test",
            config=SessionConfig(permission_mode="default"),
        )

        updated = await manager.update_template(
            template.template_id,
            docker_proxy_enabled=True,
            docker_proxy_image="proxy:latest",
            assigned_secrets=["cred-a", "cred-b"],
            docker_proxy_allowlist_domains=["example.com", "api.example.com"],
            docker_home_directory="/home/agent",
        )

        assert updated.docker_proxy_enabled is True
        assert updated.docker_proxy_image == "proxy:latest"
        assert updated.assigned_secrets == ["cred-a", "cred-b"]
        assert updated.docker_proxy_allowlist_domains == ["example.com", "api.example.com"]
        assert updated.docker_home_directory == "/home/agent"

        # Verify persistence to disk
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        reloaded = await manager2.get_template(template.template_id)
        assert reloaded.docker_proxy_allowlist_domains == ["example.com", "api.example.com"]
        assert reloaded.assigned_secrets == ["cred-a", "cred-b"]
        assert reloaded.docker_proxy_enabled is True

    @pytest.mark.asyncio
    async def test_issue_1116_update_runtime_flag_fields(self, manager):
        """Regression #1116: setting_sources, bare_mode, env_scrub_enabled must persist."""
        template = await manager.create_template(
            name="Runtime Flags Test",
            config=SessionConfig(permission_mode="default"),
        )

        updated = await manager.update_template(
            template.template_id,
            setting_sources=["user", "project"],
            bare_mode=True,
            env_scrub_enabled=True,
        )

        assert updated.setting_sources == ["user", "project"]
        assert updated.bare_mode is True
        assert updated.env_scrub_enabled is True

        # Verify persistence to disk
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        reloaded = await manager2.get_template(template.template_id)
        assert reloaded.setting_sources == ["user", "project"]
        assert reloaded.bare_mode is True
        assert reloaded.env_scrub_enabled is True


# --- Signature parity test ---


class TestCreateDefaultTemplates:
    """Verify create_default_templates passes all JSON fields through to SessionConfig."""

    @pytest.mark.asyncio
    async def test_issue_759_default_template_preserves_all_fields(self, data_dir):
        """Regression: create_default_templates must pass all JSON fields to SessionConfig.

        Before this fix, only permission_mode and allowed_tools were forwarded;
        fields like docker_enabled, auto_memory_mode, and skill_creating_enabled
        were silently dropped, causing templates to revert to defaults.
        """
        import json

        # Create a fake default_templates source directory with a rich template
        source_dir = data_dir / "default_templates"
        source_dir.mkdir()
        (source_dir / "agent.json").write_text(json.dumps({
            "name": "Agent",
            "permission_mode": "bypassPermissions",
            "allowed_tools": [],
            "disallowed_tools": [],
            "capabilities": [],
            "docker_enabled": True,
            "history_distillation_enabled": True,
            "auto_memory_mode": "session",
            "skill_creating_enabled": True,
            "role": "Autonomous agent",
            "description": "Test agent template",
        }))
        (source_dir / "agent.md").write_text("You are an agent.")

        # Patch _get_default_templates_dir to use our fake dir
        import src.template_manager as tm
        original_fn = tm._get_default_templates_dir
        tm._get_default_templates_dir = lambda: source_dir

        try:
            manager = TemplateManager(data_dir)
            await manager.load_templates()
            await manager.create_default_templates()

            agent = await manager.get_template_by_name("Agent")
            assert agent is not None, "Agent template should have been created"
            assert agent.docker_enabled is True, "docker_enabled must be True"
            assert agent.auto_memory_mode == "session", "auto_memory_mode must be 'session'"
            assert agent.skill_creating_enabled is True, "skill_creating_enabled must be True"
            assert agent.history_distillation_enabled is True
            assert agent.permission_mode == "bypassPermissions"
            assert agent.system_prompt == "You are an agent."
            assert agent.role == "Autonomous agent"
            assert agent.description == "Test agent template"
        finally:
            tm._get_default_templates_dir = original_fn


# --- MCP toggle regression tests (issue #786) ---


class TestMcpToggleRoundTrip:
    """Regression: enable_claudeai_mcp_servers and strict_mcp_config must persist."""

    def test_mcp_toggles_round_trip(self):
        """to_dict()/from_dict() must preserve non-default MCP toggle values."""
        template = MinionTemplate(
            template_id="mcp-test",
            name="MCP Toggle Test",
            permission_mode="default",
            enable_claudeai_mcp_servers=False,
            strict_mcp_config=True,
        )

        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)

        assert restored.enable_claudeai_mcp_servers is False
        assert restored.strict_mcp_config is True

    @pytest.mark.asyncio
    async def test_update_mcp_toggles_persist(self, manager):
        """update_template() must persist MCP toggle changes to disk."""
        template = await manager.create_template(
            name="MCP Update Test",
            config=SessionConfig(permission_mode="default"),
        )
        # Defaults should be True/False
        assert template.enable_claudeai_mcp_servers is True
        assert template.strict_mcp_config is False

        updated = await manager.update_template(
            template.template_id,
            enable_claudeai_mcp_servers=False,
            strict_mcp_config=True,
        )
        assert updated.enable_claudeai_mcp_servers is False
        assert updated.strict_mcp_config is True

        # Reload from disk and verify persistence
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        reloaded = await manager2.get_template(template.template_id)
        assert reloaded.enable_claudeai_mcp_servers is False
        assert reloaded.strict_mcp_config is True


# --- Import template tests (issue #797) ---


class TestImportTemplate:
    """Verify import_template round-trip, conflict detection, and overwrite."""

    @pytest.mark.asyncio
    async def test_import_roundtrip(self, manager):
        """Exported template data can be imported back as a new template."""
        original = await manager.create_template(
            name="Export Me",
            config=SessionConfig(
                permission_mode="acceptEdits",
                allowed_tools=["bash", "read"],
                model="claude-sonnet-4-20250514",
                thinking_mode="enabled",
                thinking_budget_tokens=4000,
            ),
            role="developer",
            description="A template to export",
            capabilities=["python"],
        )

        envelope = {"version": 1, "template": original.to_dict()}

        # Delete original so we can import cleanly
        await manager.delete_template(original.template_id)

        imported = await manager.import_template(envelope)

        assert imported.name == original.name
        assert imported.permission_mode == original.permission_mode
        assert imported.allowed_tools == original.allowed_tools
        assert imported.model == original.model
        assert imported.thinking_mode == original.thinking_mode
        assert imported.thinking_budget_tokens == original.thinking_budget_tokens
        assert imported.role == original.role
        assert imported.description == original.description
        assert imported.capabilities == original.capabilities
        # IDs must be fresh
        assert imported.template_id != original.template_id
        assert imported.created_at != original.created_at

    @pytest.mark.asyncio
    async def test_import_conflict_detection(self, manager):
        """Import raises TemplateConflictError when name already exists."""
        existing = await manager.create_template(
            name="Conflict Target",
            config=SessionConfig(permission_mode="default"),
        )

        envelope = {
            "version": 1,
            "template": {
                "name": "Conflict Target",
                "permission_mode": "acceptEdits",
                "allowed_tools": [],
                "disallowed_tools": [],
                "capabilities": [],
                "created_at": existing.created_at.isoformat(),
                "updated_at": existing.updated_at.isoformat(),
                "template_id": existing.template_id,
            },
        }

        with pytest.raises(TemplateConflictError) as exc_info:
            await manager.import_template(envelope, overwrite=False)

        assert exc_info.value.existing_id == existing.template_id
        assert exc_info.value.name == "Conflict Target"

    @pytest.mark.asyncio
    async def test_import_overwrite(self, manager):
        """Import with overwrite=True replaces the existing template."""
        original = await manager.create_template(
            name="Overwrite Me",
            config=SessionConfig(permission_mode="default"),
            description="original description",
        )

        envelope = {
            "version": 1,
            "template": {
                "name": "Overwrite Me",
                "permission_mode": "acceptEdits",
                "allowed_tools": ["bash"],
                "disallowed_tools": [],
                "capabilities": [],
                "description": "updated description",
                "created_at": original.created_at.isoformat(),
                "updated_at": original.updated_at.isoformat(),
                "template_id": original.template_id,
            },
        }

        imported = await manager.import_template(envelope, overwrite=True)

        assert imported.name == "Overwrite Me"
        assert imported.permission_mode == "acceptEdits"
        assert imported.allowed_tools == ["bash"]
        assert imported.description == "updated description"
        # Old template ID must be gone; new one created
        assert imported.template_id != original.template_id
        assert await manager.get_template(original.template_id) is None
        # Only one template with this name should exist
        all_templates = await manager.list_templates()
        matching = [t for t in all_templates if t.name == "Overwrite Me"]
        assert len(matching) == 1


class TestImportFieldPreservation:
    """Regression and structural tests for import_template field preservation (issue #1065)."""

    @pytest.mark.asyncio
    async def test_import_preserves_auto_memory_directory(self, manager):
        """Regression: auto_memory_directory must survive export → import round-trip."""
        original = await manager.create_template(
            name="AutoMem Test",
            config=SessionConfig(
                permission_mode="default",
                auto_memory_directory="/custom/memory/path",
            ),
        )

        envelope = {"version": 1, "template": original.to_dict()}
        await manager.delete_template(original.template_id)

        imported = await manager.import_template(envelope)

        assert imported.auto_memory_directory == "/custom/memory/path"

    @pytest.mark.asyncio
    async def test_import_preserves_all_session_config_fields(self, manager):
        """Structural: every SessionConfig field shared with MinionTemplate survives round-trip."""
        import dataclasses

        from src.models.minion_template import MinionTemplate as MinionTemplateModel

        template_fields = {f.name for f in dataclasses.fields(MinionTemplateModel)}
        config_fields = set(SessionConfig.model_fields.keys())
        shared = template_fields & config_fields
        # template_id in SessionConfig is a session→template link; in MinionTemplate it is the
        # template's own primary key (intentionally regenerated on import).  Exclude it.
        shared -= {"template_id"}

        # Build a config with non-default values for all shared fields
        config = SessionConfig(
            permission_mode="acceptEdits",
            system_prompt="Test system prompt",
            override_system_prompt=True,
            allowed_tools=["bash", "read"],
            disallowed_tools=["write"],
            model="claude-sonnet-4-20250514",
            thinking_mode="enabled",
            thinking_budget_tokens=8000,
            effort="high",
            additional_directories=["/extra"],
            cli_path="/usr/local/bin/claude",
            sandbox_enabled=True,
            sandbox_config={"network": False},
            docker_enabled=True,
            docker_image="custom:latest",
            docker_extra_mounts=["/vol:/vol"],
            history_distillation_enabled=False,
            auto_memory_mode="session",
            auto_memory_directory="/custom/memory",
            skill_creating_enabled=True,
            mcp_server_ids=["srv-1"],
            enable_claudeai_mcp_servers=False,
            strict_mcp_config=True,
        )

        original = await manager.create_template(
            name="AllFields Test",
            config=config,
            role="tester",
            description="All fields template",
        )

        envelope = {"version": 1, "template": original.to_dict()}
        await manager.delete_template(original.template_id)

        imported = await manager.import_template(envelope)

        # Verify every shared field was preserved
        for field in shared:
            original_val = getattr(original, field)
            imported_val = getattr(imported, field)
            assert imported_val == original_val, (
                f"Field '{field}' not preserved: expected {original_val!r}, got {imported_val!r}"
            )

    @pytest.mark.asyncio
    async def test_import_legacy_v1_missing_fields_defaults(self, manager):
        """Backward compat: minimal v1 envelope missing newer fields imports with defaults."""
        envelope = {
            "version": 1,
            "template": {
                "name": "Legacy Template",
                "permission_mode": "default",
                "allowed_tools": [],
                "disallowed_tools": [],
                "capabilities": [],
            },
        }

        imported = await manager.import_template(envelope)

        assert imported.name == "Legacy Template"
        assert imported.permission_mode == "default"
        # Fields absent from envelope should take SessionConfig defaults
        assert imported.auto_memory_directory is None
        assert imported.auto_memory_mode == "claude"
        assert imported.history_distillation_enabled is True
        assert imported.skill_creating_enabled is False

    @pytest.mark.asyncio
    async def test_create_template_propagates_all_config_fields(self, manager):
        """Structural: create_template() must propagate every SessionConfig field shared
        with MinionTemplate without requiring manual enumeration."""
        import dataclasses

        from src.models.minion_template import MinionTemplate as MinionTemplateModel

        template_fields = {f.name for f in dataclasses.fields(MinionTemplateModel)}
        config_fields = set(SessionConfig.model_fields.keys())
        # Fields directly managed by create_template() — these are intentionally excluded
        # from the config spread; system_prompt's direct param takes precedence.
        direct_fields = {"template_id", "name", "role", "system_prompt",
                         "description", "capabilities", "created_at", "updated_at"}
        shared = (template_fields & config_fields) - direct_fields

        config = SessionConfig(
            permission_mode="acceptEdits",
            auto_memory_directory="/verify/path",
            auto_memory_mode="session",
            history_distillation_enabled=False,
            skill_creating_enabled=True,
            enable_claudeai_mcp_servers=False,
            strict_mcp_config=True,
        )

        template = await manager.create_template(
            name="Propagation Test",
            config=config,
        )

        # Spot-check key fields that were previously missing
        assert template.auto_memory_directory == "/verify/path"
        assert template.auto_memory_mode == "session"
        assert template.history_distillation_enabled is False
        assert template.skill_creating_enabled is True
        assert template.enable_claudeai_mcp_servers is False
        assert template.strict_mcp_config is True

        # Structural check: all shared fields must match config values.
        # MinionTemplate.__post_init__ normalizes None list fields to [] — treat as equivalent.
        config_dump = config.model_dump()
        for field in shared:
            if field not in config_dump:
                continue
            expected = config_dump[field]
            actual = getattr(template, field)
            if expected is None and isinstance(actual, list) and len(actual) == 0:
                expected = []
            assert actual == expected, (
                f"Field '{field}' not propagated: expected {expected!r}, got {actual!r}"
            )


class TestSignatureParity:
    """Ensure create_template and update_template accept all MinionTemplate fields.

    This is the test that would have caught the missing thinking_mode parameter.
    It introspects the dataclass fields and verifies the manager methods accept them.
    """

    # Fields that are auto-managed, not user-settable via create/update
    AUTO_FIELDS = {"template_id", "created_at", "updated_at"}

    def _get_template_field_names(self):
        """Get all MinionTemplate field names except auto-managed ones."""
        import dataclasses
        return {
            f.name for f in dataclasses.fields(MinionTemplate)
        } - self.AUTO_FIELDS

    def test_create_accepts_all_template_fields(self):
        """create_template() must accept every settable MinionTemplate field.

        Config-level fields (permission_mode, model, etc.) are now passed via
        the ``config: SessionConfig`` parameter rather than as individual kwargs.
        We verify that every template field is covered by either a direct
        create_template() parameter or a SessionConfig field.
        """
        import inspect

        sig = inspect.signature(TemplateManager.create_template)
        create_params = set(sig.parameters.keys()) - {"self"}
        session_config_fields = set(SessionConfig.model_fields.keys())
        template_fields = self._get_template_field_names()

        # Fields covered by direct params OR SessionConfig
        covered = (create_params | session_config_fields) - {"config"}
        missing = template_fields - covered
        assert missing == set(), (
            f"create_template() is missing parameters for MinionTemplate fields: {missing}"
        )

    def test_update_accepts_all_template_fields(self):
        """update_template() must accept every settable MinionTemplate field."""
        import inspect
        sig = inspect.signature(TemplateManager.update_template)
        update_params = set(sig.parameters.keys()) - {"self"}
        template_fields = self._get_template_field_names()

        # update_template has template_id instead of the individual ID
        update_params.discard("template_id")
        template_fields.discard("template_id")

        # update needs template_id as positional, which we already removed
        missing = template_fields - update_params
        assert missing == set(), (
            f"update_template() is missing parameters for MinionTemplate fields: {missing}"
        )


# --- Orchestrator retirement tests ---


class TestCreateDefaultTemplatesRetirement:
    """Verify create_default_templates() retires the Orchestrator template."""

    @pytest.mark.asyncio
    async def test_create_default_templates_retires_orchestrator(self, manager):
        """create_default_templates() deletes the Orchestrator template when it exists."""
        config = SessionConfig(permission_mode="default")
        orchestrator = await manager.create_template(
            name="Orchestrator",
            config=config,
            system_prompt="Old orchestrator prompt",
        )
        assert orchestrator is not None
        assert "Orchestrator" in {t.name for t in manager.templates.values()}

        # Calling create_default_templates() should retire it
        await manager.create_default_templates()

        assert "Orchestrator" not in {t.name for t in manager.templates.values()}

    @pytest.mark.asyncio
    async def test_create_default_templates_does_not_overwrite_existing_prompt(self, manager):
        """create_default_templates() never overwrites an existing system_prompt."""
        # Seed one of the real default templates manually with a custom prompt
        config = SessionConfig(permission_mode="default")
        custom_prompt = "Custom prompt that must not be overwritten"
        existing = await manager.create_template(
            name="Issue Planner",
            config=config,
            system_prompt=custom_prompt,
        )
        assert existing is not None

        await manager.create_default_templates()

        updated = manager.templates.get(existing.template_id)
        assert updated is not None
        assert updated.system_prompt == custom_prompt


# --- Issue #1059 Phase 2: Template deletion guard tests ---


def _make_session_info(
    session_id: str,
    template_id: str | None,
    state: SessionState = SessionState.ACTIVE,
    name: str | None = None,
) -> SessionInfo:
    now = datetime.now(UTC)
    return SessionInfo(
        session_id=session_id,
        state=state,
        created_at=now,
        updated_at=now,
        template_id=template_id,
        name=name,
    )


def _make_session_manager(sessions: list[SessionInfo]) -> AsyncMock:
    """Return a mock SessionManager whose list_sessions() returns the given sessions."""
    sm = AsyncMock()
    sm.list_sessions = AsyncMock(return_value=sessions)
    return sm


@pytest.mark.asyncio
class TestTemplateDeletionGuard:
    """Tests #12-15 — deletion guard for templates with linked sessions."""

    async def test_delete_template_no_linked_sessions(self, manager):
        """Template with no linked sessions deletes successfully."""
        config = SessionConfig(permission_mode="default")
        template = await manager.create_template(name="Lonely Template", config=config)
        sm = _make_session_manager([])  # No sessions

        result = await manager.delete_template(template.template_id, session_manager=sm)

        assert result is True
        assert template.template_id not in manager.templates

    async def test_delete_template_blocked_by_active_session(self, manager):
        """Template with an ACTIVE session raises TemplateInUseError."""
        config = SessionConfig(permission_mode="default")
        template = await manager.create_template(name="Busy Template", config=config)
        session = _make_session_info("sess-001", template.template_id, state=SessionState.ACTIVE, name="My Session")
        sm = _make_session_manager([session])

        with pytest.raises(TemplateInUseError) as exc_info:
            await manager.delete_template(template.template_id, session_manager=sm)

        err = exc_info.value
        assert err.template_id == template.template_id
        assert "sess-001" in err.session_ids
        assert "My Session" in err.session_names
        # Template must still exist after failed deletion
        assert template.template_id in manager.templates

    async def test_delete_template_terminated_sessions_dont_block(self, manager):
        """Template with only TERMINATED sessions deletes successfully."""
        config = SessionConfig(permission_mode="default")
        template = await manager.create_template(name="Done Template", config=config)
        terminated_session = _make_session_info(
            "sess-done", template.template_id, state=SessionState.TERMINATED
        )
        sm = _make_session_manager([terminated_session])

        result = await manager.delete_template(template.template_id, session_manager=sm)

        assert result is True
        assert template.template_id not in manager.templates

    async def test_delete_template_error_lists_sessions(self, manager):
        """TemplateInUseError contains correct session_ids and session_names."""
        config = SessionConfig(permission_mode="default")
        template = await manager.create_template(name="Multi Session Template", config=config)
        sessions = [
            _make_session_info("sess-a", template.template_id, state=SessionState.ACTIVE, name="Alpha"),
            _make_session_info("sess-b", template.template_id, state=SessionState.PAUSED, name="Beta"),
            # Terminated one — should not be in blocking list
            _make_session_info("sess-c", template.template_id, state=SessionState.TERMINATED, name="Done"),
        ]
        sm = _make_session_manager(sessions)

        with pytest.raises(TemplateInUseError) as exc_info:
            await manager.delete_template(template.template_id, session_manager=sm)

        err = exc_info.value
        assert set(err.session_ids) == {"sess-a", "sess-b"}
        assert set(err.session_names) == {"Alpha", "Beta"}

    async def test_delete_template_no_session_manager_skips_guard(self, manager):
        """Without session_manager, deletion proceeds without guard (backward compat)."""
        config = SessionConfig(permission_mode="default")
        template = await manager.create_template(name="Compat Template", config=config)

        # No session_manager passed — guard not applied
        result = await manager.delete_template(template.template_id)

        assert result is True
        assert template.template_id not in manager.templates

    async def test_delete_template_other_template_sessions_dont_block(self, manager):
        """Sessions linked to a different template don't block deletion."""
        config = SessionConfig(permission_mode="default")
        target = await manager.create_template(name="Target Template", config=config)
        other = await manager.create_template(name="Other Template", config=config)

        other_session = _make_session_info("sess-other", other.template_id, state=SessionState.ACTIVE)
        sm = _make_session_manager([other_session])

        result = await manager.delete_template(target.template_id, session_manager=sm)

        assert result is True
        assert target.template_id not in manager.templates
