"""
Tests for TemplateManager - template CRUD and MinionTemplate model.

Covers:
- MinionTemplate round-trip serialization (all fields)
- TemplateManager.create_template() with all parameters
- TemplateManager.update_template() with all parameters
- Field propagation: every MinionTemplate field must be accepted by create/update
"""

import tempfile
from pathlib import Path

import pytest

from src.models.minion_template import MinionTemplate
from src.session_config import SessionConfig
from src.template_manager import TemplateManager


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
        import dataclasses
        import inspect

        sig = inspect.signature(TemplateManager.create_template)
        create_params = set(sig.parameters.keys()) - {"self"}
        session_config_fields = {f.name for f in dataclasses.fields(SessionConfig)}
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
