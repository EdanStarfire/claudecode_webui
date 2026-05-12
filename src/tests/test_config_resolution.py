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
    """Build a minimal SessionInfo for testing.

    CONFIG_FIELDS kwargs go into session.config; non-CONFIG_FIELDS stay flat.
    session_overrides (legacy name) are merged into config.
    current_permission_mode is set flat AND copied into config["permission_mode"]
    so that resolve_effective_config can read it on the non-template path.
    """
    now = datetime.now(UTC)
    config: dict = {}
    flat: dict = {}

    for k, v in kwargs.items():
        if k in CONFIG_FIELDS:
            config[k] = v
        elif k == "current_permission_mode":
            flat[k] = v
            # Also write into config so resolver can see it on the non-template path
            config.setdefault("permission_mode", v)
        else:
            flat[k] = v

    # Merge legacy session_overrides into config (higher priority)
    if session_overrides:
        config.update(session_overrides)

    return SessionInfo(
        session_id="test-session",
        state=SessionState.CREATED,
        created_at=now,
        updated_at=now,
        template_id=template_id,
        config=config,
        **flat,
    )


def _make_template(**kwargs) -> MinionTemplate:
    """Build a minimal MinionTemplate for testing.

    All CONFIG_FIELDS kwargs go into template.config; identity/lifecycle fields
    stay flat (template_id, name, profile_ids, watchdog).
    """
    identity_fields = {"template_id", "name", "profile_ids", "watchdog", "role", "description",
                       "capabilities", "created_at", "updated_at"}
    # Deprecated: template_overrides are merged into config
    template_overrides = kwargs.pop("template_overrides", None) or {}

    config = {k: v for k, v in kwargs.items() if k in CONFIG_FIELDS}
    config.update(template_overrides)
    identity = {k: v for k, v in kwargs.items() if k in identity_fields}

    defaults = {"template_id": "tmpl-001", "name": "Test Template"}
    defaults.update(identity)
    defaults["config"] = config
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
        """MinionTemplate identity/lifecycle fields must not overlap with CONFIG_FIELDS.

        Post-#1230: all CONFIG_FIELDS live in MinionTemplate.config (a dict), not as
        flat dataclass fields. The flat fields are identity/lifecycle only.
        This test verifies that the flat MinionTemplate fields do NOT appear in
        CONFIG_FIELDS (they are intentionally excluded).
        """
        template_flat_fields = {f.name for f in dataclasses.fields(MinionTemplate)}
        config_field_names = set(SessionConfig.model_fields.keys())

        # Fields that appear on BOTH MinionTemplate flat schema and SessionConfig
        shared = template_flat_fields & config_field_names

        # Known intentional exclusions from CONFIG_FIELDS (identity/lifecycle fields
        # that may still appear on SessionConfig but are NOT config values)
        excluded_from_config_fields = {
            "template_id",       # Identity field
            "working_directory",  # Session-only
        }

        # Flat MinionTemplate fields that appear in CONFIG_FIELDS would be a bug
        # (they should be in MinionTemplate.config dict, not flat).
        unexpected_in_config_fields = (shared - excluded_from_config_fields) & CONFIG_FIELDS
        assert unexpected_in_config_fields == set(), (
            f"These MinionTemplate flat fields should NOT be in CONFIG_FIELDS "
            f"(they must live in MinionTemplate.config dict instead): "
            f"{unexpected_in_config_fields}"
        )


# ---- Issue #1062: 3-tier resolution tests ----


@pytest.mark.asyncio
class TestResolveWithProfile:
    """Test #7 — profile values used as base when profile assigned to template area."""

    async def test_profile_value_used_when_assigned(self):
        """Profile value is used when template does not explicitly set the field in config."""
        profile = _make_profile(
            area="model",
            config={"model": "claude-opus-4-5"},
        )
        pm = _make_profile_manager([profile])
        # Template has profile assigned for model area but does NOT set model in config
        # (so profile value fills in from layer 1a)
        template = _make_template(
            profile_ids={"model": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"  # Profile fills in (template has no model set)

    async def test_profile_missing_field_falls_back_to_template_flat(self):
        """Profile config may omit fields; template config value fills in."""
        profile = _make_profile(
            area="model",
            config={"thinking_mode": "enabled"},  # Only provides thinking_mode
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            model="claude-sonnet-4-6",  # Template config sets model (profile omits it)
            # Note: template does NOT set thinking_mode, so profile's value wins
            profile_ids={"model": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-sonnet-4-6"  # Template config (profile omits this)
        assert result.thinking_mode == "enabled"     # Profile value (template omits this)

    async def test_area_without_profile_uses_template_flat(self):
        """Areas with no profile assigned use template config values."""
        profile = _make_profile(area="model", config={"model": "claude-opus-4-5"})
        pm = _make_profile_manager([profile])
        # Template assigns profile to 'model' area; permission_mode from template config
        # Template does NOT set model in config → profile fills it in
        template = _make_template(
            permission_mode="acceptEdits",
            profile_ids={"model": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"        # From profile (template has no model)
        assert result.permission_mode == "acceptEdits"  # From template config (no profile for permissions)


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
        """Profile assigned to model area only; permissions uses template config."""
        model_profile = _make_profile(
            profile_id="model-p",
            area="model",
            config={"model": "claude-opus-4-5", "effort": "high"},
        )
        pm = _make_profile_manager([model_profile])
        # Template does NOT set model or effort (profile fills those in)
        # Template sets permission_mode (no profile for permissions area)
        template = _make_template(
            permission_mode="default",    # Template config (no profile for permissions)
            profile_ids={"model": "model-p"},  # Only model area has profile
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.model == "claude-opus-4-5"  # From profile (template omits model)
        assert result.effort == "high"            # From profile (template omits effort)
        assert result.permission_mode == "default"  # From template config


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
        """With profile, profile value is used when template does not set the field."""
        profile = _make_profile(area="model", config={"model": "claude-sonnet-4-6"})
        pm = _make_profile_manager([profile])
        # Template does NOT set model in config → profile fills it in
        template = _make_template(
            profile_ids={"model": profile.profile_id},
        )
        result = await resolve_template_config(template, pm)
        assert result["model"] == "claude-sonnet-4-6"  # Profile fills in (template has no model)

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


@pytest.mark.asyncio
class TestTagFieldReplaceSemantics:
    """Test #15 — tag-style list fields use last-wins (replace) semantics (issue #1223).

    docker_proxy_allowlist_domains and assigned_secrets no longer use additive merge;
    they follow standard last-wins precedence like all other config fields.
    """

    async def test_template_override_replaces_profile_secrets(self):
        """template_overrides.assigned_secrets replaces (not unions) the profile value."""
        profile = _make_profile(
            area="isolation",
            config={"assigned_secrets": ["ssh_test"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            template_overrides={"assigned_secrets": ["ssh_test2"]},
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.assigned_secrets == ["ssh_test2"]

    async def test_template_override_replaces_profile_allowlist_domains(self):
        """template_overrides.docker_proxy_allowlist_domains replaces (not unions) the profile value."""
        profile = _make_profile(
            area="isolation",
            config={"docker_proxy_allowlist_domains": "profile.com"},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            template_overrides={"docker_proxy_allowlist_domains": ["override.com"]},
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.docker_proxy_allowlist_domains == ["override.com"]

    async def test_template_omits_field_inherits_profile_secrets(self):
        """When template sets no override, profile assigned_secrets is inherited unchanged."""
        profile = _make_profile(
            area="isolation",
            config={"assigned_secrets": ["ssh_test"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.assigned_secrets == ["ssh_test"]

    async def test_template_omits_field_inherits_profile_allowlist_domains(self):
        """When template sets no override, profile docker_proxy_allowlist_domains is inherited."""
        profile = _make_profile(
            area="isolation",
            config={"docker_proxy_allowlist_domains": ["profile.com"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.docker_proxy_allowlist_domains == ["profile.com"]

    async def test_template_override_empty_list_produces_empty_secrets(self):
        """template_overrides.assigned_secrets=[] resolves to [] (not None fallback to profile)."""
        profile = _make_profile(
            area="isolation",
            config={"assigned_secrets": ["ssh_test"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            template_overrides={"assigned_secrets": []},
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.assigned_secrets == []

    async def test_template_override_empty_list_produces_empty_allowlist_domains(self):
        """template_overrides.docker_proxy_allowlist_domains=[] resolves to []."""
        profile = _make_profile(
            area="isolation",
            config={"docker_proxy_allowlist_domains": ["profile.com"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            template_overrides={"docker_proxy_allowlist_domains": []},
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.docker_proxy_allowlist_domains == []

    async def test_session_override_replaces_profile_value(self):
        """session_overrides fully replaces (not unions) the profile value for tag fields."""
        profile = _make_profile(
            area="isolation",
            config={"assigned_secrets": ["ssh_test"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(
            template_id="tmpl-001",
            session_overrides={"assigned_secrets": ["session_secret"]},
        )
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm, pm)

        assert config.assigned_secrets == ["session_secret"]

    async def test_template_override_supersedes_template_flat_field(self):
        """template_overrides wins over template flat field for tag fields."""
        template = _make_template(
            assigned_secrets=["flat_secret"],
            template_overrides={"assigned_secrets": ["override_secret"]},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        config = await resolve_effective_config(session, tm)

        assert config.assigned_secrets == ["override_secret"]


@pytest.mark.asyncio
class TestLeanSessionInfo:
    """Issue #1059: resolve_effective_config works correctly with lean SessionInfo.

    A "lean" SessionInfo has all CONFIG_FIELDS at their dataclass defaults
    (None, False, [], "claude", etc.) and derives all config values from
    the template + profiles via resolve_effective_config().
    """

    def _make_lean_session(self, template_id: str = "tmpl-lean", session_overrides: dict | None = None) -> "SessionInfo":
        """Build a SessionInfo with CONFIG_FIELDS at dataclass defaults (lean).

        session_overrides is placed into session.config (the new unified dict).
        """
        from datetime import UTC, datetime

        from ..session_manager import SessionInfo, SessionState
        now = datetime.now(UTC)
        return SessionInfo(
            session_id="lean-session",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            working_directory="/work",
            current_permission_mode="acceptEdits",
            initial_permission_mode="acceptEdits",
            template_id=template_id,
            config=dict(session_overrides or {}),
        )

    async def test_lean_session_derives_model_from_template(self):
        """Lean session resolves model from template even though session.model is None."""
        template = _make_template(model="claude-opus-4-5")
        session = self._make_lean_session()
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.model == "claude-opus-4-5"

    async def test_lean_session_derives_system_prompt_from_template(self):
        """Lean session resolves system_prompt from template."""
        template = _make_template(system_prompt="You are a code assistant.")
        session = self._make_lean_session()
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.system_prompt == "You are a code assistant."

    async def test_lean_session_override_wins_over_template(self):
        """session_overrides values take precedence over template values for lean sessions."""
        template = _make_template(model="claude-sonnet-4-6")
        session = self._make_lean_session(session_overrides={"model": "claude-opus-4-5"})
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.model == "claude-opus-4-5"

    async def test_lean_session_proxy_fields_from_template(self):
        """Lean session correctly resolves docker_proxy_allowlist_domains from template."""
        template = _make_template(
            docker_proxy_enabled=True,
            docker_proxy_allowlist_domains=["api.example.com", "cdn.example.com"],
        )
        session = self._make_lean_session()
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.docker_proxy_enabled is True
        assert "api.example.com" in (result.docker_proxy_allowlist_domains or [])

    async def test_lean_session_all_config_fields_at_defaults_still_resolves(self):
        """Lean session with minimal template resolves without error."""
        template = _make_template()  # Minimal template
        session = self._make_lean_session()
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert isinstance(result, SessionConfig)
        # Template default permission_mode should be carried through
        assert result.permission_mode == "acceptEdits"


class TestIssue1170AssignedSecretsInIsolation:
    """Regression tests for issue #1170 — assigned_secrets belongs to isolation area."""

    def test_issue_1170_secrets_area_removed(self):
        """The 'secrets' area must not exist in PROFILE_AREAS (issue #1170)."""
        assert "secrets" not in PROFILE_AREAS

    def test_issue_1170_assigned_secrets_in_isolation_area(self):
        """assigned_secrets must now be in the 'isolation' area (issue #1170)."""
        assert FIELD_TO_AREA.get("assigned_secrets") == "isolation"
        assert "assigned_secrets" in PROFILE_AREAS["isolation"]

    @pytest.mark.asyncio
    async def test_issue_1170_isolation_profile_with_assigned_secrets_resolves(self):
        """Creating a profile with area='isolation' and assigned_secrets must not fail."""
        profile = _make_profile(
            area="isolation",
            config={"assigned_secrets": ["my-secret"]},
        )
        pm = _make_profile_manager([profile])
        template = _make_template(
            profile_ids={"isolation": profile.profile_id},
        )
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm, pm)

        assert result.assigned_secrets == ["my-secret"]


class TestIssue1396ExtraEnvResolution:
    """Tests for extra_env config resolution (issue #1396)."""

    @pytest.mark.asyncio
    async def test_template_extra_env_merges_to_config(self):
        """Template extra_env is reflected in the resolved SessionConfig."""
        template_env = {"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.test"}
        template = _make_template(extra_env=template_env)
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.extra_env == template_env

    @pytest.mark.asyncio
    async def test_session_extra_env_overrides_template(self):
        """Session-level extra_env overrides (not merges with) the template's value."""
        template_env = {"GIT_AUTHOR_NAME": "Template"}
        session_env = {"GIT_AUTHOR_NAME": "Session", "CUSTOM_VAR": "value"}

        template = _make_template(extra_env=template_env)
        session = _make_session(template_id="tmpl-001", extra_env=session_env)
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.extra_env == session_env

    @pytest.mark.asyncio
    async def test_template_missing_extra_env_defaults_none(self):
        """Template without extra_env produces None on the resolved config."""
        template = _make_template()  # no extra_env set
        session = _make_session(template_id="tmpl-001")
        tm = _make_template_manager(template)

        result = await resolve_effective_config(session, tm)

        assert result.extra_env is None
