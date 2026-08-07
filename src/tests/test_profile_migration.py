"""
Tests for the isolation->features field migration — Issue #1707.

Covers the idempotent ProfileManager.load_profiles() migration that promotes
extra_env/max_subagent_spawn_depth off any pre-existing area="isolation" profile
(these fields were reclassified to area="features"):

1. Referenced-profile promotion (single template)
2. Multi-template fan-out
3. Orphan-profile creation
4. Idempotency on second load_profiles() call
5. Orphan-profile name-collision guard
6. resolve_effective_config() regression (identical resolved value before/after)
7. create_profile/update_profile area validation post-reclassification
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.config_resolution import resolve_effective_config
from src.models.config_profile import ConfigProfile
from src.profile_manager import ProfileManager
from src.session_config import SessionConfig
from src.session_manager import SessionInfo, SessionState
from src.slug_utils import slugify
from src.template_manager import TemplateManager


@pytest.fixture
def data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def manager(data_dir):
    return ProfileManager(data_dir)


@pytest.fixture
def template_manager(data_dir):
    return TemplateManager(data_dir)


def _write_legacy_isolation_profile(data_dir: Path, name: str, config: dict) -> str:
    """Write a profile JSON straight to disk, bypassing area validation.

    Simulates a pre-#1707 isolation profile that still carries extra_env and/or
    max_subagent_spawn_depth — data that predates the field reclassification and
    would be rejected by today's create_profile()/update_profile().
    """
    now = datetime.now(UTC)
    profile_id = f"legacy-{slugify(name)}"
    profile = ConfigProfile(
        profile_id=profile_id,
        name=name,
        area="isolation",
        config=config,
        created_at=now,
        updated_at=now,
    )
    profiles_dir = data_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    json_file = profiles_dir / f"{slugify(name)}.json"
    with open(json_file, "w") as f:
        json.dump(profile.to_dict(), f, indent=2)
    return profile_id


def _make_session_info(template_id: str) -> SessionInfo:
    now = datetime.now(UTC)
    return SessionInfo(
        session_id="test-session",
        state=SessionState.CREATED,
        created_at=now,
        updated_at=now,
        template_id=template_id,
    )


@pytest.mark.asyncio
class TestMigrationReferencedPromotion:
    async def test_single_referencing_template_receives_override(
        self, data_dir, manager, template_manager
    ):
        profile_id = _write_legacy_isolation_profile(
            data_dir,
            "Legacy Isolation",
            {"extra_env": {"FOO": "bar"}, "max_subagent_spawn_depth": 2, "bare_mode": True},
        )
        template = await template_manager.create_template(
            name="Dependent Template",
            config=SessionConfig(permission_mode="default"),
            profile_ids={"isolation": profile_id},
        )

        await manager.load_profiles(template_manager=template_manager)

        migrated_profile = await manager.get_profile(profile_id)
        assert migrated_profile is not None
        assert "extra_env" not in migrated_profile.config
        assert "max_subagent_spawn_depth" not in migrated_profile.config
        assert migrated_profile.config.get("bare_mode") is True  # untouched field survives

        updated_template = await template_manager.get_template(template.template_id)
        assert updated_template.config.get("extra_env") == {"FOO": "bar"}
        assert updated_template.config.get("max_subagent_spawn_depth") == 2


@pytest.mark.asyncio
class TestMigrationMultiTemplateFanOut:
    async def test_all_referencing_templates_receive_override(
        self, data_dir, manager, template_manager
    ):
        profile_id = _write_legacy_isolation_profile(
            data_dir, "Shared Isolation", {"extra_env": {"A": "1"}}
        )
        t1 = await template_manager.create_template(
            name="T1",
            config=SessionConfig(permission_mode="default"),
            profile_ids={"isolation": profile_id},
        )
        t2 = await template_manager.create_template(
            name="T2",
            config=SessionConfig(permission_mode="default"),
            profile_ids={"isolation": profile_id},
        )
        # Unrelated template must not receive the override
        t3 = await template_manager.create_template(
            name="Unrelated",
            config=SessionConfig(permission_mode="default"),
        )

        await manager.load_profiles(template_manager=template_manager)

        u1 = await template_manager.get_template(t1.template_id)
        u2 = await template_manager.get_template(t2.template_id)
        u3 = await template_manager.get_template(t3.template_id)
        assert u1.config.get("extra_env") == {"A": "1"}
        assert u2.config.get("extra_env") == {"A": "1"}
        assert u3.config.get("extra_env") is None

        migrated_profile = await manager.get_profile(profile_id)
        assert "extra_env" not in migrated_profile.config


@pytest.mark.asyncio
class TestMigrationOrphanProfileCreation:
    async def test_orphan_profile_creates_new_features_profile(
        self, data_dir, manager, template_manager
    ):
        profile_id = _write_legacy_isolation_profile(
            data_dir, "Orphan Isolation", {"max_subagent_spawn_depth": 3}
        )

        await manager.load_profiles(template_manager=template_manager)

        original = await manager.get_profile(profile_id)
        assert original is not None
        assert "max_subagent_spawn_depth" not in original.config

        new_profiles = [
            p for p in await manager.list_profiles()
            if p.name == "Orphan Isolation (migrated)"
        ]
        assert len(new_profiles) == 1
        assert new_profiles[0].area == "features"
        assert new_profiles[0].config == {"max_subagent_spawn_depth": 3}


@pytest.mark.asyncio
class TestMigrationIdempotency:
    async def test_second_load_after_restart_is_noop(self, data_dir, manager, template_manager):
        profile_id = _write_legacy_isolation_profile(
            data_dir, "Idempotent Isolation", {"extra_env": {"X": "1"}}
        )

        await manager.load_profiles(template_manager=template_manager)
        first_names = sorted(p.name for p in await manager.list_profiles())

        # Fresh ProfileManager re-reading from disk, simulating a server restart.
        manager2 = ProfileManager(data_dir)
        await manager2.load_profiles(template_manager=template_manager)
        second_names = sorted(p.name for p in await manager2.list_profiles())

        assert second_names == first_names
        migrated = [p for p in await manager2.list_profiles() if "(migrated)" in p.name]
        assert len(migrated) == 1

        original = await manager2.get_profile(profile_id)
        assert "extra_env" not in original.config

    async def test_second_load_after_restart_is_noop_for_referenced_profile(
        self, data_dir, manager, template_manager
    ):
        """Idempotency on the primary (referenced-template) migration path, not just orphans."""
        profile_id = _write_legacy_isolation_profile(
            data_dir, "Idempotent Referenced", {"max_subagent_spawn_depth": 3}
        )
        template = await template_manager.create_template(
            name="Idempotent Referenced Template",
            config={"permission_mode": "default"},
            profile_ids={"isolation": profile_id},
        )

        await manager.load_profiles(template_manager=template_manager)
        first = await template_manager.get_template(template.template_id)
        assert first.config.get("max_subagent_spawn_depth") == 3

        # Fresh managers re-reading from disk, simulating a server restart.
        template_manager2 = TemplateManager(data_dir)
        await template_manager2.load_templates()
        manager2 = ProfileManager(data_dir)
        await manager2.load_profiles(template_manager=template_manager2)

        second = await template_manager2.get_template(template.template_id)
        assert second.config.get("max_subagent_spawn_depth") == 3  # unchanged, not re-applied
        assert second.updated_at == first.updated_at  # update_template was NOT called again

        original = await manager2.get_profile(profile_id)
        assert "max_subagent_spawn_depth" not in original.config


@pytest.mark.asyncio
class TestMigrationPartialFailureIsolation:
    async def test_one_referencing_template_update_failure_leaves_source_unstripped(
        self, data_dir, manager, template_manager, monkeypatch
    ):
        """Crash-safety: if a dependent write fails mid-profile, the source isolation
        profile must NOT be stripped, so the next load_profiles() retries it — and
        other profiles migrated in the same pass must be unaffected."""
        broken_id = _write_legacy_isolation_profile(
            data_dir, "Broken Isolation", {"extra_env": {"A": "1"}}
        )
        healthy_id = _write_legacy_isolation_profile(
            data_dir, "Healthy Isolation", {"extra_env": {"B": "2"}}
        )
        broken_template = await template_manager.create_template(
            name="Broken Template",
            config={"permission_mode": "default"},
            profile_ids={"isolation": broken_id},
        )
        await template_manager.create_template(
            name="Healthy Template",
            config={"permission_mode": "default"},
            profile_ids={"isolation": healthy_id},
        )

        original_update_template = template_manager.update_template

        async def flaky_update_template(template_id, **kwargs):
            if template_id == broken_template.template_id:
                raise RuntimeError("simulated write failure")
            return await original_update_template(template_id, **kwargs)

        monkeypatch.setattr(template_manager, "update_template", flaky_update_template)

        await manager.load_profiles(template_manager=template_manager)

        # Broken profile: dependent write failed, so the source must stay unstripped.
        broken_profile = await manager.get_profile(broken_id)
        assert broken_profile.config.get("extra_env") == {"A": "1"}

        # Healthy profile in the same pass must have migrated normally, unaffected
        # by the other profile's failure.
        healthy_profile = await manager.get_profile(healthy_id)
        assert "extra_env" not in healthy_profile.config
        healthy_template = await template_manager.get_template_by_name("Healthy Template")
        assert healthy_template.config.get("extra_env") == {"B": "2"}

        # Retry (no more failure injected): the broken profile completes migration.
        monkeypatch.setattr(template_manager, "update_template", original_update_template)
        await manager.load_profiles(template_manager=template_manager)
        broken_profile_retry = await manager.get_profile(broken_id)
        assert "extra_env" not in broken_profile_retry.config
        broken_template_after = await template_manager.get_template(broken_template.template_id)
        assert broken_template_after.config.get("extra_env") == {"A": "1"}


@pytest.mark.asyncio
class TestMigrationNameCollisionGuard:
    async def test_existing_migrated_name_skips_creation_but_still_strips_source(
        self, data_dir, manager, template_manager
    ):
        existing = await manager.create_profile(
            name="Collide (migrated)",
            area="features",
            config={"auto_memory_mode": "session"},
        )
        profile_id = _write_legacy_isolation_profile(
            data_dir, "Collide", {"extra_env": {"Y": "2"}}
        )

        await manager.load_profiles(template_manager=template_manager)

        collide_profiles = [
            p for p in await manager.list_profiles() if p.name == "Collide (migrated)"
        ]
        assert len(collide_profiles) == 1
        assert collide_profiles[0].profile_id == existing.profile_id
        assert collide_profiles[0].config == {"auto_memory_mode": "session"}  # untouched

        original = await manager.get_profile(profile_id)
        assert "extra_env" not in original.config


@pytest.mark.asyncio
class TestMigrationResolveEffectiveConfigRegression:
    async def test_resolved_value_unchanged_before_and_after_migration(
        self, data_dir, manager, template_manager
    ):
        profile_id = _write_legacy_isolation_profile(
            data_dir,
            "Resolve Isolation",
            {"extra_env": {"K": "V"}, "max_subagent_spawn_depth": 2},
        )
        # A plain dict config (only the explicitly-set key) so the profile's
        # extra_env/max_subagent_spawn_depth values are actually inherited pre-migration,
        # instead of being masked by a full SessionConfig() default dump.
        template = await template_manager.create_template(
            name="Resolve Template",
            config={"permission_mode": "default"},
            profile_ids={"isolation": profile_id},
        )
        session_info = _make_session_info(template.template_id)

        # Before migration: fresh manager loaded without template_manager, so the
        # migration pass never runs and the profile still carries the raw fields.
        pre_manager = ProfileManager(data_dir)
        await pre_manager.load_profiles()
        before = await resolve_effective_config(session_info, template_manager, pre_manager)

        # After migration: fields promoted onto the template as explicit overrides.
        await manager.load_profiles(template_manager=template_manager)
        after = await resolve_effective_config(session_info, template_manager, manager)

        assert before.extra_env == after.extra_env == {"K": "V"}
        assert before.max_subagent_spawn_depth == after.max_subagent_spawn_depth == 2


@pytest.mark.asyncio
class TestAreaValidationPostReclassification:
    async def test_create_profile_rejects_migrated_fields_for_isolation(self, manager):
        with pytest.raises(ValueError, match="not valid for area"):
            await manager.create_profile(
                name="Bad Isolation", area="isolation", config={"extra_env": {"A": "1"}}
            )
        with pytest.raises(ValueError, match="not valid for area"):
            await manager.create_profile(
                name="Bad Isolation 2",
                area="isolation",
                config={"max_subagent_spawn_depth": 2},
            )

    async def test_create_profile_accepts_migrated_fields_for_features(self, manager):
        profile = await manager.create_profile(
            name="Good Features",
            area="features",
            config={"extra_env": {"A": "1"}, "max_subagent_spawn_depth": 2},
        )
        assert profile.config == {"extra_env": {"A": "1"}, "max_subagent_spawn_depth": 2}

    async def test_update_profile_rejects_migrated_fields_for_isolation(self, manager):
        profile = await manager.create_profile(name="Isolation Profile", area="isolation", config={})
        with pytest.raises(ValueError, match="not valid for area"):
            await manager.update_profile(profile.profile_id, config={"extra_env": {"A": "1"}})

    async def test_update_profile_accepts_migrated_fields_for_features(self, manager):
        profile = await manager.create_profile(name="Features Profile", area="features", config={})
        updated = await manager.update_profile(
            profile.profile_id, config={"max_subagent_spawn_depth": 3}
        )
        assert updated.config == {"max_subagent_spawn_depth": 3}
