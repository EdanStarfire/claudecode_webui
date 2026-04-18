"""
Tests for config_resolution module — Issue #1059 Phase 2 / Issue #1062 Phase 2.

Covers:
1. Legacy path (no template): config built from flat session fields
2. Template path (no overrides): config built from template fields
3. Template path with overrides: overrides win over template values
4. Deleted template fallback: falls back to flat session fields
5. Override subset: only overridden fields differ from template
6. Structural test: CONFIG_FIELDS covers all shared fields between MinionTemplate and SessionConfig
7. 3-tier: profile values used as base when profile assigned
8. 3-tier: template_overrides win over profile values
9. 3-tier: session_overrides win over template_overrides and profile
10. 3-tier: profile cache (no redundant loads)
11. 3-tier: mixed areas (some with profiles, some without)
12. 3-tier: profile deleted → fallback to template flat fields
13. PROFILE_AREAS structural: union equals CONFIG_FIELDS
14. resolve_template_config: shared helper for spawn path
"""

import dataclasses
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ..config_resolution import (
    CONFIG_FIELDS,
    FIELD_TO_AREA,
    PROFILE_AREAS,
    resolve_effective_config,
    resolve_template_config,
)
from ..models.config_profile import ConfigProfile
from ..models.minion_template import MinionTemplate
from ..session_config import SessionConfig
from ..session_manager import SessionInfo, SessionState


def _make_profile(
    profile_id: str = "profile-001",
    area: str = "model",
    config: dict | None = None,
) -> ConfigProfile:
    """Build a minimal ConfigProfile for testing."""
    now = datetime.now(UTC)
    return ConfigProfile(
        profile_id=profile_id,
        name=f"Test {area} Profile",
        area=area,
        config=config or {},
        created_at=now,
        updated_at=now,
    )


def _make_profile_manager(profiles: list[ConfigProfile]) -> AsyncMock:
    """Return a mock ProfileManager."""
    pm = AsyncMock()
    profile_map = {p.profile_id: p for p in profiles}

    async def get_profile(pid):
        return profile_map.get(pid)

    pm.get_profile = AsyncMock(side_effect=get_profile)
    return pm


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
        - Identity fields: template_id, name, role, description, capabilities
        - Composition fields: profile_ids, template_overrides
        - Lifecycle fields: created_at, updated_at
        - Session-only fields: working_directory (always from session, never from template)
        """
        template_fields = {f.name for f in dataclasses.fields(MinionTemplate)}
        config_field_names = set(SessionConfig.model_fields.keys())

        # Fields that appear on BOTH MinionTemplate and SessionConfig
        shared = template_fields & config_field_names

        # Known intentional exclusions from CONFIG_FIELDS
        excluded_from_config_fields = {
            "template_id",       # Identity field
            "name",              # Identity field — not on SessionConfig anyway
            "role",              # Identity — not on SessionConfig
            "description",       # Identity — not on SessionConfig
            "capabilities",      # Identity — not on SessionConfig
            "profile_ids",       # Composition metadata, not a config value
            "template_overrides",  # Composition metadata, not a config value
            "created_at",        # Lifecycle
            "updated_at",        # Lifecycle
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


# ---- Issue #1062: 3-tier resolution tests ----


@pytest.mark.asyncio
class TestResolveWithProfile:
    """Test #7 — profile values used as base when profile assigned to template area."""

    async def test_profile_value_used_when_assigned(self):
        """Profile value overrides template flat field when profile assigned."""
        profile = _make_profile(
            area="model",
            config={"model": "claude-opus-4-5"},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            model="claude-haiku-4-5",  # Template flat value (lower priority)
            profile_ids={"model": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"  # Profile wins over template flat

    async def test_profile_missing_field_falls_back_to_template_flat(self):
        """Profile config may omit fields; template flat value fills in."""
        profile = _make_profile(
            area="model",
            config={"thinking_mode": "enabled"},  # Only overrides thinking_mode
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            model="claude-sonnet-4-6",
            thinking_mode="auto",  # Will be overridden by profile
            profile_ids={"model": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-sonnet-4-6"  # Template flat (profile omits this)
        assert result.thinking_mode == "enabled"     # Profile value

    async def test_area_without_profile_uses_template_flat(self):
        """Areas with no profile assigned use template flat values."""
        profile = _make_profile(area="model", config={"model": "claude-opus-4-5"})
        pm = _make_profile_manager([profile])
        # Template only assigns profile to 'model' area, not 'permissions'
        template = _make_template(
            permission_mode="acceptEdits",
            model="claude-haiku-4-5",
            profile_ids={"model": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"        # From profile
        assert result.permission_mode == "acceptEdits"  # From template flat (no profile)


@pytest.mark.asyncio
class TestResolveTemplateOverridesProfile:
    """Test #8 — template_overrides win over profile values."""

    async def test_template_override_wins_over_profile(self):
        """template_overrides has higher priority than profile config."""
        profile = _make_profile(area="model", config={"model": "claude-opus-4-5"})
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"model": profile.profile_id},
            template_overrides={"model": "claude-sonnet-4-6"},  # Override profile value
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-sonnet-4-6"  # template_overrides wins

    async def test_template_override_subset(self):
        """Only overridden fields differ; rest come from profile."""
        profile = _make_profile(
            area="model",
            config={"model": "claude-opus-4-5", "thinking_mode": "enabled"},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"model": profile.profile_id},
            template_overrides={"thinking_mode": "auto"},  # Only override thinking_mode
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"  # From profile
        assert result.thinking_mode == "auto"     # template_override wins


@pytest.mark.asyncio
class TestResolveSessionOverridesAll:
    """Test #9 — session_overrides win over template_overrides and profile."""

    async def test_session_override_wins_over_template_override_and_profile(self):
        """session_overrides is highest priority in all 3 tiers."""
        profile = _make_profile(area="model", config={"model": "claude-opus-4-5"})
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"model": profile.profile_id},
            template_overrides={"model": "claude-sonnet-4-6"},
        )
        session = _make_session(
            template_id="tmpl-001",
            session_overrides={"model": "claude-haiku-4-5"},  # Highest priority
        )
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-haiku-4-5"  # session_overrides wins


@pytest.mark.asyncio
class TestResolveMixedAreas:
    """Test #11 — mixed areas (some with profiles, some without) per-area resolution."""

    async def test_mixed_areas(self):
        """Profile assigned to model area only; permissions uses template flat."""
        model_profile = _make_profile(
            profile_id="model-p",
            area="model",
            config={"model": "claude-opus-4-5", "effort": "high"},
        )
        pm = _make_profile_manager([model_profile])
        template = _make_template(
            model="claude-haiku-4-5",    # Template flat (overridden by profile)
            effort="low",                 # Template flat (overridden by profile)
            permission_mode="default",    # No profile for permissions area
            profile_ids={"model": "model-p"},  # Only model area has profile
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"  # From profile
        assert result.effort == "high"            # From profile
        assert result.permission_mode == "default"  # From template flat


@pytest.mark.asyncio
class TestResolveProfileDeleted:
    """Test #12 — profile deleted after template creation → fallback to template flat."""

    async def test_profile_deleted_fallback(self):
        """When profile is not found, falls back to template flat value."""
        pm = _make_profile_manager([])  # Empty — profile "deleted"
        template = _make_template(
            model="claude-haiku-4-5",  # Template flat value
            profile_ids={"model": "deleted-profile-id"},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-haiku-4-5"  # Falls back to template flat


@pytest.mark.asyncio
class TestResolveNoProfileManager:
    """Test: no profile_manager provided → 2-tier behavior (backward compat)."""

    async def test_no_profile_manager_uses_template(self):
        """Without profile_manager, resolution behaves as 2-tier (template + session_overrides)."""
        template = _make_template(
            permission_mode="plan",
            model="claude-opus-4-5",
            profile_ids={"model": "some-id"},  # Profile assigned but can't be resolved
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        # No profile_manager passed
        result = await resolve_effective_config(session, tm)

        assert result.permission_mode == "plan"
        assert result.model == "claude-opus-4-5"  # Template flat used (no profile_manager)


@pytest.mark.asyncio
class TestProfileCacheWithinResolution:
    """Test #10 — profile is loaded at most once per profile_id per resolution call."""

    async def test_profile_loaded_once_per_resolution(self):
        """Same profile_id referenced in multiple areas loads the profile only once."""
        profile = _make_profile(
            profile_id="shared-profile",
            area="model",
            config={"model": "claude-opus-4-5"},
        )
        pm = _make_profile_manager([profile])
        # Template references same profile_id in two different fields
        # (Though model area is only one, we test that get_profile isn't called per-field)
        template = _make_template(
            profile_ids={"model": "shared-profile"},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        await resolve_effective_config(session, tm, pm)

        # get_profile called at most once for the shared-profile id
        call_count = sum(
            1 for call in pm.get_profile.call_args_list
            if call.args[0] == "shared-profile"
        )
        assert call_count == 1


class TestProfileAreasStructure:
    """Test #13 — PROFILE_AREAS structural integrity."""

    def test_profile_areas_union_equals_config_fields(self):
        """Union of all PROFILE_AREAS sets must exactly equal CONFIG_FIELDS."""
        all_area_fields = set(FIELD_TO_AREA.keys())
        assert all_area_fields == CONFIG_FIELDS

    def test_no_field_in_multiple_areas(self):
        """Each field belongs to exactly one area."""
        seen = {}
        for area, fields in PROFILE_AREAS.items():
            for field in fields:
                assert field not in seen, (
                    f"Field '{field}' appears in multiple areas: '{seen[field]}' and '{area}'"
                )
                seen[field] = area

    def test_all_areas_non_empty(self):
        """All 6 profile areas are non-empty."""
        assert len(PROFILE_AREAS) == 6
        for area, fields in PROFILE_AREAS.items():
            assert len(fields) > 0, f"Area '{area}' has no fields"


@pytest.mark.asyncio
class TestResolveTemplateConfig:
    """Test #14 — resolve_template_config shared helper."""

    async def test_resolve_template_config_no_profile(self):
        """Without profile, returns template flat values."""
        template = _make_template(
            model="claude-opus-4-5",
            permission_mode="plan",
        )
        result = await resolve_template_config(template)
        assert result["model"] == "claude-opus-4-5"
        assert result["permission_mode"] == "plan"

    async def test_resolve_template_config_with_profile(self):
        """With profile, profile values override template flat values."""
        profile = _make_profile(area="model", config={"model": "claude-sonnet-4-6"})
        pm = _make_profile_manager([profile])
        template = _make_template(
            model="claude-haiku-4-5",  # Template flat (lower priority)
            profile_ids={"model": profile.profile_id},
        )
        result = await resolve_template_config(template, pm)
        assert result["model"] == "claude-sonnet-4-6"  # Profile wins

    async def test_resolve_template_config_template_overrides_win(self):
        """template_overrides have priority over profile values."""
        profile = _make_profile(area="model", config={"model": "claude-opus-4-5"})
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"model": profile.profile_id},
            template_overrides={"model": "claude-haiku-4-5"},
        )
        result = await resolve_template_config(template, pm)
        assert result["model"] == "claude-haiku-4-5"  # template_overrides wins
