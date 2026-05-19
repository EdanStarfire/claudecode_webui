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
            role="tester",
            description="A test template",
            capabilities=["python", "testing"],
            config={
                "permission_mode": "default",
                "allowed_tools": ["bash", "read"],
                "disallowed_tools": ["write"],
                "system_prompt": "You are a tester.",
                "model": "claude-sonnet-4-20250514",
                "override_system_prompt": True,
                "sandbox_enabled": True,
                "sandbox_config": {"network": False},
                "cli_path": "/usr/bin/claude",
                "docker_enabled": True,
                "docker_image": "claude-code:local",
                "docker_extra_mounts": ["/data:/data:ro"],
                "thinking_mode": "enabled",
                "thinking_budget_tokens": 5000,
                "effort": "high",
            },
        )

        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)

        assert restored.template_id == "test-id"
        assert restored.name == "Test Template"
        assert restored.config.get("permission_mode") == "default"
        assert restored.config.get("allowed_tools") == ["bash", "read"]
        assert restored.config.get("disallowed_tools") == ["write"]
        assert restored.role == "tester"
        assert restored.config.get("system_prompt") == "You are a tester."
        assert restored.description == "A test template"
        assert restored.config.get("model") == "claude-sonnet-4-20250514"
        assert restored.capabilities == ["python", "testing"]
        assert restored.config.get("override_system_prompt") is True
        assert restored.config.get("sandbox_enabled") is True
        assert restored.config.get("sandbox_config") == {"network": False}
        assert restored.config.get("cli_path") == "/usr/bin/claude"
        assert restored.config.get("docker_enabled") is True
        assert restored.config.get("docker_image") == "claude-code:local"
        assert restored.config.get("docker_extra_mounts") == ["/data:/data:ro"]
        assert restored.config.get("thinking_mode") == "enabled"
        assert restored.config.get("thinking_budget_tokens") == 5000
        assert restored.config.get("effort") == "high"

    def test_effort_max_normalizes_to_high(self):
        """Legacy flat-field effort='max' at the top level is migrated into config as-is.

        The migration layer (_migrate_template_to_config_dict) promotes flat CONFIG_FIELDS
        into the config dict without normalization.  Callers that stored effort='max' on
        disk will have it preserved in config['effort'] after migration + from_dict.
        """
        from src.template_manager import _migrate_template_to_config_dict

        # Simulate a legacy JSON file on disk with effort='max' as a flat field
        raw = {
            "name": "Legacy Template",
            "template_id": "legacy",
            "permission_mode": "default",
            "effort": "max",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        migrated, changed = _migrate_template_to_config_dict(raw)
        assert changed is True
        restored = MinionTemplate.from_dict(migrated)
        # effort is promoted into config by migration; no renaming occurs at this layer
        assert restored.config.get("effort") == "max"

    def test_none_defaults_round_trip(self):
        """Fields with None defaults should survive round-trip."""
        template = MinionTemplate(
            template_id="minimal",
            name="Minimal",
            config={"permission_mode": "default"},
        )

        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)

        assert restored.config.get("thinking_mode") is None
        assert restored.config.get("thinking_budget_tokens") is None
        assert restored.config.get("effort") is None
        assert restored.config.get("cli_path") is None
        assert restored.config.get("docker_enabled", False) is False
        assert restored.config.get("docker_image") is None


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
        assert template.config.get("thinking_mode") == "enabled"
        assert template.config.get("thinking_budget_tokens") == 8000
        assert template.config.get("effort") == "high"
        assert template.config.get("docker_enabled") is True
        assert template.config.get("cli_path") == "/usr/bin/claude"

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
        assert t.config.get("thinking_mode") == "enabled"
        assert t.config.get("thinking_budget_tokens") == 4000
        assert t.config.get("effort") == "medium"

    @pytest.mark.asyncio
    async def test_create_minimal(self, manager):
        """Create with only required fields should not raise."""
        template = await manager.create_template(
            name="Minimal",
            config=SessionConfig(permission_mode="default"),
        )

        assert template.config.get("thinking_mode") is None
        assert template.config.get("thinking_budget_tokens") is None
        assert template.config.get("effort") is None


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

        assert updated.config.get("thinking_mode") == "enabled"
        assert updated.config.get("thinking_budget_tokens") == 10000
        assert updated.config.get("effort") == "high"

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
        assert t.config.get("thinking_mode") == "enabled"
        assert t.config.get("thinking_budget_tokens") == 6000
        assert t.config.get("effort") == "low"

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
        assert updated.config.get("thinking_mode") == "enabled"
        assert updated.config.get("thinking_budget_tokens") == 12000
        assert updated.config.get("effort") == "high"
        assert updated.config.get("docker_enabled") is True
        assert updated.config.get("cli_path") == "/opt/claude"

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

        assert updated.config.get("docker_proxy_enabled") is True
        assert updated.config.get("docker_proxy_image") == "proxy:latest"
        assert updated.config.get("assigned_secrets") == ["cred-a", "cred-b"]
        assert updated.config.get("docker_proxy_allowlist_domains") == ["example.com", "api.example.com"]
        assert updated.config.get("docker_home_directory") == "/home/agent"

        # Verify persistence to disk
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        reloaded = await manager2.get_template(template.template_id)
        assert reloaded.config.get("docker_proxy_allowlist_domains") == ["example.com", "api.example.com"]
        assert reloaded.config.get("assigned_secrets") == ["cred-a", "cred-b"]
        assert reloaded.config.get("docker_proxy_enabled") is True

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

        assert updated.config.get("setting_sources") == ["user", "project"]
        assert updated.config.get("bare_mode") is True
        assert updated.config.get("env_scrub_enabled") is True

        # Verify persistence to disk
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        reloaded = await manager2.get_template(template.template_id)
        assert reloaded.config.get("setting_sources") == ["user", "project"]
        assert reloaded.config.get("bare_mode") is True
        assert reloaded.config.get("env_scrub_enabled") is True


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
            assert agent.config.get("docker_enabled") is True, "docker_enabled must be True"
            assert agent.config.get("auto_memory_mode") == "session", "auto_memory_mode must be 'session'"
            assert agent.config.get("skill_creating_enabled") is True, "skill_creating_enabled must be True"
            # history_distillation_enabled=True is the default; migration omits defaults from config.
            # Fall back to the SessionConfig default (True) when absent.
            assert agent.config.get("history_distillation_enabled", True) is True
            assert agent.config.get("permission_mode") == "bypassPermissions"
            # system_prompt is stored in the .md companion file.  After _save_template the
            # in-memory template.config loses the key (shared-dict mutation).  Reload from
            # disk so that load_templates() repopulates system_prompt from .md.
            manager2 = TemplateManager(data_dir)
            await manager2.load_templates()
            agent2 = await manager2.get_template_by_name("Agent")
            assert agent2 is not None
            assert agent2.config.get("system_prompt") == "You are an agent."
            assert agent2.role == "Autonomous agent"
            assert agent2.description == "Test agent template"
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
            config={
                "permission_mode": "default",
                "enable_claudeai_mcp_servers": False,
                "strict_mcp_config": True,
            },
        )

        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)

        assert restored.config.get("enable_claudeai_mcp_servers") is False
        assert restored.config.get("strict_mcp_config") is True

    @pytest.mark.asyncio
    async def test_update_mcp_toggles_persist(self, manager):
        """update_template() must persist MCP toggle changes to disk."""
        template = await manager.create_template(
            name="MCP Update Test",
            config=SessionConfig(permission_mode="default"),
        )
        # Defaults should be True/False
        assert template.config.get("enable_claudeai_mcp_servers", True) is True
        assert template.config.get("strict_mcp_config", False) is False

        updated = await manager.update_template(
            template.template_id,
            enable_claudeai_mcp_servers=False,
            strict_mcp_config=True,
        )
        assert updated.config.get("enable_claudeai_mcp_servers") is False
        assert updated.config.get("strict_mcp_config") is True

        # Reload from disk and verify persistence
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        reloaded = await manager2.get_template(template.template_id)
        assert reloaded.config.get("enable_claudeai_mcp_servers") is False
        assert reloaded.config.get("strict_mcp_config") is True


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
        assert imported.config.get("permission_mode") == original.config.get("permission_mode")
        assert imported.config.get("allowed_tools") == original.config.get("allowed_tools")
        assert imported.config.get("model") == original.config.get("model")
        assert imported.config.get("thinking_mode") == original.config.get("thinking_mode")
        assert imported.config.get("thinking_budget_tokens") == original.config.get("thinking_budget_tokens")
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
                # Use a non-default permission_mode so migration does not drop it
                # (migration omits fields whose values equal the SessionConfig default).
                "permission_mode": "bypassPermissions",
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
        assert imported.config.get("permission_mode") == "bypassPermissions"
        assert imported.config.get("allowed_tools") == ["bash"]
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
        """Regression: auto_memory_directory must survive export -> import round-trip."""
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

        assert imported.config.get("auto_memory_directory") == "/custom/memory/path"

    @pytest.mark.asyncio
    async def test_import_preserves_all_session_config_fields(self, manager):
        """Structural: every SessionConfig field shared with MinionTemplate survives round-trip."""
        import dataclasses

        from src.models.minion_template import MinionTemplate as MinionTemplateModel

        template_fields = {f.name for f in dataclasses.fields(MinionTemplateModel)}
        config_fields = set(SessionConfig.model_fields.keys())
        shared = template_fields & config_fields
        # template_id in SessionConfig is a session->template link; in MinionTemplate it is the
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
        # Shared fields are CONFIG_FIELDS that live in .config, not flat attributes
        for field in shared:
            original_val = original.config.get(field)
            imported_val = imported.config.get(field)
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
        assert imported.config.get("permission_mode") == "default"
        # Fields absent from envelope should take SessionConfig defaults
        assert imported.config.get("auto_memory_directory") is None
        assert imported.config.get("auto_memory_mode", "claude") == "claude"
        assert imported.config.get("history_distillation_enabled", True) is True
        assert imported.config.get("skill_creating_enabled", False) is False

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
        assert template.config.get("auto_memory_directory") == "/verify/path"
        assert template.config.get("auto_memory_mode") == "session"
        assert template.config.get("history_distillation_enabled") is False
        assert template.config.get("skill_creating_enabled") is True
        assert template.config.get("enable_claudeai_mcp_servers") is False
        assert template.config.get("strict_mcp_config") is True

        # Structural check: all shared CONFIG_FIELDS must match config values.
        # These live in template.config, not as flat attributes.
        from src.session_config import CONFIG_FIELDS
        config_dump = config.model_dump()
        for field in shared:
            if field not in config_dump or field not in CONFIG_FIELDS:
                continue
            expected = config_dump[field]
            actual = template.config.get(field)
            # MinionTemplate stores only non-default values; default-valued fields may be absent.
            # Only assert when the config value differs from the SessionConfig default.
            if actual is None and expected is None:
                continue
            if actual == expected:
                continue
            # If actual is missing (None) but expected is the SessionConfig default, that's fine.
            from src.session_config import DEFAULTS
            if actual is None and expected == DEFAULTS.get(field):
                continue
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

        Config-level fields (permission_mode, model, etc.) are now stored in
        template.config (a dict) and passed via the ``config: SessionConfig``
        parameter rather than as individual kwargs.  The ``config`` field itself
        is intentionally excluded from the coverage check — it is the container,
        not a separately-settable field.
        """
        import inspect

        sig = inspect.signature(TemplateManager.create_template)
        create_params = set(sig.parameters.keys()) - {"self"}
        session_config_fields = set(SessionConfig.model_fields.keys())
        template_fields = self._get_template_field_names()

        # Exclude 'config' — it is the dict container for CONFIG_FIELDS, not a
        # separately-settable field; it is covered by the SessionConfig parameter.
        template_fields -= {"config"}

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

        # 'config' is the dict container for CONFIG_FIELDS — update_template accepts
        # it directly as a kwarg for full replacement, so it IS in update_params.
        # 'is_default' is server-managed (set only by create_default_templates() at
        # seed time) and is intentionally absent from update_template()'s signature.
        template_fields -= {"config", "is_default"}

        # update needs template_id as positional, which we already removed
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
        """create_default_templates() never overwrites an existing system_prompt.

        system_prompt is stored in the .md companion file.  After create_template()
        the value is saved to disk and the in-memory template.config no longer holds
        it (the save helper pops it to write it to .md).  Verify persistence by
        reloading from disk via a fresh TemplateManager instance.
        """
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

        # Reload from disk so that system_prompt is populated from the .md file
        manager2 = TemplateManager(manager.templates_dir.parent)
        await manager2.load_templates()
        updated = manager2.templates.get(existing.template_id)
        assert updated is not None
        assert updated.config.get("system_prompt") == custom_prompt


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


# --- Issue #1230: Migration tests ---


class TestMigrationToConfigDict:
    """Test _migrate_template_to_config_dict — pre-1230 flat fields → config dict."""

    def test_flat_non_default_fields_promoted_to_config(self):
        """Non-default flat CONFIG_FIELDS are promoted into config dict.

        SessionConfig defaults: permission_mode="acceptEdits", docker_enabled=False, etc.
        We use "bypassPermissions" (non-default) to test promotion.
        """
        from src.template_manager import _migrate_template_to_config_dict

        raw = {
            "template_id": "tmpl-001",
            "name": "Legacy Template",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "permission_mode": "bypassPermissions",   # non-default (default is "acceptEdits")
            "model": "claude-sonnet-4-20250514",      # non-default (default is None)
            "docker_enabled": True,                   # non-default (default is False)
        }

        migrated, changed = _migrate_template_to_config_dict(raw)

        assert changed is True
        assert "config" in migrated
        assert migrated["config"]["permission_mode"] == "bypassPermissions"
        assert migrated["config"]["model"] == "claude-sonnet-4-20250514"
        assert migrated["config"]["docker_enabled"] is True
        # Flat keys must be removed
        assert "permission_mode" not in migrated
        assert "model" not in migrated
        assert "docker_enabled" not in migrated

    def test_default_valued_flat_fields_dropped(self):
        """Flat fields whose value equals the SessionConfig default are NOT promoted.

        SessionConfig defaults: permission_mode="acceptEdits", docker_enabled=False,
        history_distillation_enabled=True.
        """
        from src.template_manager import _migrate_template_to_config_dict

        raw = {
            "template_id": "tmpl-002",
            "name": "Defaults Only",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "permission_mode": "acceptEdits",          # default — should be dropped
            "docker_enabled": False,                   # default — should be dropped
            "history_distillation_enabled": True,      # default — should be dropped
        }

        migrated, changed = _migrate_template_to_config_dict(raw)

        assert changed is True
        # Config should be empty (all fields were default)
        assert migrated.get("config") == {}

    def test_template_overrides_promoted_unconditionally(self):
        """template_overrides entries are promoted even if equal to defaults."""
        from src.template_manager import _migrate_template_to_config_dict

        raw = {
            "template_id": "tmpl-003",
            "name": "Override Test",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "template_overrides": {
                "model": "claude-opus-4-7",
                "bare_mode": True,
            },
        }

        migrated, changed = _migrate_template_to_config_dict(raw)

        assert changed is True
        assert migrated["config"]["model"] == "claude-opus-4-7"
        assert migrated["config"]["bare_mode"] is True
        assert "template_overrides" not in migrated

    def test_idempotent_when_config_already_present(self):
        """Migration is a no-op when 'config' key already exists."""
        from src.template_manager import _migrate_template_to_config_dict

        raw = {
            "template_id": "tmpl-004",
            "name": "Already Migrated",
            "config": {"permission_mode": "acceptEdits"},
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

        migrated, changed = _migrate_template_to_config_dict(raw)

        assert changed is False
        assert migrated is raw  # Same object returned unchanged

    def test_full_round_trip_flat_to_config(self):
        """Pre-1230 template with flat fields + template_overrides round-trips correctly."""
        from src.template_manager import _migrate_template_to_config_dict

        raw = {
            "template_id": "tmpl-005",
            "name": "Full Legacy",
            "role": "Developer",
            "capabilities": ["python"],
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "permission_mode": "bypassPermissions",  # non-default ("acceptEdits" is default)
            "model": "claude-opus-4-7",             # non-default (None is default)
            "docker_enabled": True,                 # non-default (False is default)
            "template_overrides": {"bare_mode": True},
        }

        migrated, changed = _migrate_template_to_config_dict(raw)
        assert changed is True

        template = MinionTemplate.from_dict(migrated)
        assert template.config["permission_mode"] == "bypassPermissions"
        assert template.config["model"] == "claude-opus-4-7"
        assert template.config["docker_enabled"] is True
        assert template.config["bare_mode"] is True
        assert template.role == "Developer"
        assert template.capabilities == ["python"]
