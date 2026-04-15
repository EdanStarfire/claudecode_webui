"""
Tests for config_resolution module — Issue #1059 Phase 2.

Covers:
1. Legacy path (no template): config built from flat session fields
2. Template path (no overrides): config built from template fields
3. Template path with overrides: overrides win over template values
4. Deleted template fallback: falls back to flat session fields
5. Override subset: only overridden fields differ from template
6. Structural test: CONFIG_FIELDS covers all shared fields between MinionTemplate and SessionConfig
"""

import dataclasses
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ..config_resolution import CONFIG_FIELDS, resolve_effective_config
from ..models.minion_template import MinionTemplate
from ..session_config import SessionConfig
from ..session_manager import SessionInfo, SessionState


def _make_session(
    template_id: str | None = None,
    session_overrides: dict | None = None,
    **kwargs,
) -> SessionInfo:
    """Build a minimal SessionInfo for testing."""
    now = datetime.now(UTC)
    return SessionInfo(
        session_id="test-session",
        state=SessionState.CREATED,
        created_at=now,
        updated_at=now,
        template_id=template_id,
        session_overrides=session_overrides or {},
        **kwargs,
    )


def _make_template(**kwargs) -> MinionTemplate:
    """Build a minimal MinionTemplate for testing."""
    defaults = {
        "template_id": "tmpl-001",
        "name": "Test Template",
        "permission_mode": "acceptEdits",
    }
    defaults.update(kwargs)
    return MinionTemplate(**defaults)


def _make_template_manager(template: MinionTemplate | None) -> AsyncMock:
    """Return a mock TemplateManager whose get_template returns the given template."""
    tm = AsyncMock()
    tm.get_template = AsyncMock(return_value=template)
    return tm


@pytest.mark.asyncio
class TestResolveNoTemplate:
    """Test #1 — no template linked → legacy path."""

    async def test_resolve_no_template(self):
        """Session without template_id uses flat session fields."""
        session = _make_session(
            template_id=None,
            current_permission_mode="default",
            model="claude-opus-4-5",
        )
        tm = _make_template_manager(None)

        result = await resolve_effective_config(session, tm)

        assert isinstance(result, SessionConfig)
        assert result.permission_mode == "default"
        assert result.model == "claude-opus-4-5"
        # get_template should not be called when template_id is None
        tm.get_template.assert_not_called()

    async def test_resolve_no_template_carries_all_flat_fields(self):
        """Legacy path copies all expected fields from session."""
        session = _make_session(
            template_id=None,
            current_permission_mode="bypassPermissions",
            allowed_tools=["bash", "read"],
            sandbox_enabled=True,
            history_distillation_enabled=False,
            bare_mode=True,
        )
        tm = _make_template_manager(None)

        result = await resolve_effective_config(session, tm)

        assert result.permission_mode == "bypassPermissions"
        assert result.allowed_tools == ["bash", "read"]
        assert result.sandbox_enabled is True
        assert result.history_distillation_enabled is False
        assert result.bare_mode is True


@pytest.mark.asyncio
class TestResolveFromTemplate:
    """Test #2 — session with template, no overrides → template values win."""

    async def test_resolve_from_template(self):
        """Template values are used when session has no overrides."""
        template = _make_template(
            permission_mode="plan",
            model="claude-opus-4-5",
            allowed_tools=["bash"],
            history_distillation_enabled=False,
        )
        session = _make_session(template_id="tmpl-001", session_overrides={})
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.permission_mode == "plan"
        assert result.model == "claude-opus-4-5"
        assert result.allowed_tools == ["bash"]
        assert result.history_distillation_enabled is False

    async def test_resolve_template_id_carried_through(self):
        """template_id is preserved in effective config."""
        template = _make_template(template_id="tmpl-999")
        session = _make_session(template_id="tmpl-999", session_overrides={})
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.template_id == "tmpl-999"


@pytest.mark.asyncio
class TestResolveTemplateWithOverrides:
    """Test #3 — template + overrides → overrides win."""

    async def test_resolve_template_with_overrides(self):
        """session_overrides values supersede template values."""
        template = _make_template(
            permission_mode="acceptEdits",
            model="claude-sonnet-4-6",
        )
        session = _make_session(
            template_id="tmpl-001",
            session_overrides={"permission_mode": "bypassPermissions", "model": "claude-opus-4-5"},
        )
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.permission_mode == "bypassPermissions"
        assert result.model == "claude-opus-4-5"

    async def test_non_overridden_fields_come_from_template(self):
        """Fields not in session_overrides still come from template."""
        template = _make_template(
            permission_mode="default",
            sandbox_enabled=True,
            allowed_tools=["bash", "read"],
        )
        session = _make_session(
            template_id="tmpl-001",
            session_overrides={"permission_mode": "plan"},  # Only override permission_mode
        )
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.permission_mode == "plan"         # Override wins
        assert result.sandbox_enabled is True           # From template
        assert result.allowed_tools == ["bash", "read"] # From template


@pytest.mark.asyncio
class TestResolveTemplateDeleted:
    """Test #4 — template referenced but no longer exists → fallback to flat fields."""

    async def test_resolve_template_deleted_falls_back(self):
        """When template is not found, falls back to flat session fields."""
        session = _make_session(
            template_id="deleted-tmpl",
            current_permission_mode="default",
            model="claude-opus-4-5",
        )
        tm = _make_template_manager(None)  # template not found

        result = await resolve_effective_config(session, tm)

        assert result.permission_mode == "default"
        assert result.model == "claude-opus-4-5"
        tm.get_template.assert_called_once_with("deleted-tmpl")


@pytest.mark.asyncio
class TestResolveOverrideSubset:
    """Test #5 — only overridden fields differ; rest come from template."""

    async def test_override_subset(self):
        """Partial overrides leave non-overridden fields from template intact."""
        template = _make_template(
            permission_mode="acceptEdits",
            model="claude-haiku-4-5",
            skill_creating_enabled=False,
            auto_memory_mode="disabled",
        )
        session = _make_session(
            template_id="tmpl-001",
            session_overrides={"skill_creating_enabled": True},
        )
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.skill_creating_enabled is True         # Override
        assert result.permission_mode == "acceptEdits"       # Template
        assert result.model == "claude-haiku-4-5"            # Template
        assert result.auto_memory_mode == "disabled"         # Template


class TestConfigFieldsCompleteness:
    """Test #6 — structural test: CONFIG_FIELDS covers all shared fields."""

    def test_config_fields_covers_shared_miniontemplate_sessionconfig_fields(self):
        """CONFIG_FIELDS must include every field present on both MinionTemplate and SessionConfig.

        Excluded from CONFIG_FIELDS (intentional):
        - Identity fields: template_id, name, role, description, capabilities, profile_id
        - Lifecycle fields: created_at, updated_at
        - Session-only fields: working_directory (always from session, never from template)
        """
        template_fields = {f.name for f in dataclasses.fields(MinionTemplate)}
        config_field_names = set(SessionConfig.model_fields.keys())

        # Fields that appear on BOTH MinionTemplate and SessionConfig
        shared = template_fields & config_field_names

        # Known intentional exclusions from CONFIG_FIELDS
        excluded_from_config_fields = {
            "template_id",    # Identity field
            "name",           # Identity field — not on SessionConfig anyway
            "role",           # Identity — not on SessionConfig
            "description",    # Identity — not on SessionConfig
            "capabilities",   # Identity — not on SessionConfig
            "profile_id",     # Phase 3 concern
            "created_at",     # Lifecycle
            "updated_at",     # Lifecycle
            "working_directory",  # Session-only
        }

        # Every shared field (minus intentional exclusions) must be in CONFIG_FIELDS
        expected_in_config = shared - excluded_from_config_fields
        missing = expected_in_config - CONFIG_FIELDS
        assert missing == set(), (
            f"CONFIG_FIELDS is missing fields that exist on both MinionTemplate "
            f"and SessionConfig: {missing}. Add them to CONFIG_FIELDS or to the "
            f"excluded_from_config_fields set above if intentionally excluded."
        )
