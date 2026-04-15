"""
Tests for ProfileManager — Issue #1062.

Covers:
- Profile CRUD (create, read, update, delete)
- ConfigProfile area validation (reject invalid area, reject config keys outside area)
- Profile deletion guard (block when templates reference, allow when none do)
- MinionTemplate migration (old profile_id field handled gracefully)
- Template create/update with profile_ids and template_overrides
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.config_resolution import PROFILE_AREAS
from src.models.config_profile import ConfigProfile
from src.models.minion_template import MinionTemplate
from src.profile_manager import ProfileInUseError, ProfileManager
from src.session_config import SessionConfig
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


# ---- ConfigProfile model ----


class TestConfigProfileRoundTrip:
    def test_to_dict_from_dict(self):
        now = datetime.now(UTC)
        profile = ConfigProfile(
            profile_id="test-id",
            name="My Model Profile",
            area="model",
            config={"model": "claude-opus-4-5", "thinking_mode": "enabled"},
            created_at=now,
            updated_at=now,
        )
        data = profile.to_dict()
        restored = ConfigProfile.from_dict(data)
        assert restored.profile_id == "test-id"
        assert restored.name == "My Model Profile"
        assert restored.area == "model"
        assert restored.config == {"model": "claude-opus-4-5", "thinking_mode": "enabled"}
        assert restored.created_at == now
        assert restored.updated_at == now


# ---- ProfileManager CRUD ----


@pytest.mark.asyncio
class TestProfileCreate:
    async def test_create_profile(self, manager):
        profile = await manager.create_profile(
            name="Model Profile",
            area="model",
            config={"model": "claude-opus-4-5"},
        )
        assert profile.profile_id
        assert profile.name == "Model Profile"
        assert profile.area == "model"
        assert profile.config == {"model": "claude-opus-4-5"}

    async def test_create_profile_empty_config(self, manager):
        profile = await manager.create_profile(name="Empty", area="features", config={})
        assert profile.config == {}

    async def test_create_profile_duplicate_name_raises(self, manager):
        await manager.create_profile(name="Same Name", area="model", config={})
        with pytest.raises(ValueError, match="already exists"):
            await manager.create_profile(name="Same Name", area="permissions", config={})

    async def test_create_profile_invalid_area_raises(self, manager):
        with pytest.raises(ValueError, match="Invalid area"):
            await manager.create_profile(name="Bad", area="nonexistent", config={})

    async def test_create_profile_invalid_config_keys_raises(self, manager):
        with pytest.raises(ValueError, match="not valid for area"):
            await manager.create_profile(
                name="Bad Keys",
                area="model",
                config={"permission_mode": "default"},  # belongs to 'permissions', not 'model'
            )

    async def test_create_profile_all_areas(self, manager):
        for area in PROFILE_AREAS:
            profile = await manager.create_profile(
                name=f"Profile {area}",
                area=area,
                config={},
            )
            assert profile.area == area


@pytest.mark.asyncio
class TestProfileRead:
    async def test_get_profile_by_id(self, manager):
        created = await manager.create_profile(name="Get Me", area="mcp", config={})
        fetched = await manager.get_profile(created.profile_id)
        assert fetched is not None
        assert fetched.profile_id == created.profile_id

    async def test_get_nonexistent_returns_none(self, manager):
        result = await manager.get_profile("does-not-exist")
        assert result is None

    async def test_list_profiles_all(self, manager):
        await manager.create_profile(name="P1", area="model", config={})
        await manager.create_profile(name="P2", area="permissions", config={})
        profiles = await manager.list_profiles()
        assert len(profiles) == 2

    async def test_list_profiles_filtered_by_area(self, manager):
        await manager.create_profile(name="Model P", area="model", config={})
        await manager.create_profile(name="Perm P", area="permissions", config={})
        model_profiles = await manager.list_profiles(area="model")
        assert len(model_profiles) == 1
        assert model_profiles[0].name == "Model P"


@pytest.mark.asyncio
class TestProfileUpdate:
    async def test_update_name(self, manager):
        profile = await manager.create_profile(name="Old Name", area="model", config={})
        updated = await manager.update_profile(profile.profile_id, name="New Name")
        assert updated.name == "New Name"

    async def test_update_config(self, manager):
        profile = await manager.create_profile(name="Config Test", area="model", config={})
        updated = await manager.update_profile(
            profile.profile_id, config={"model": "claude-opus-4-5"}
        )
        assert updated.config == {"model": "claude-opus-4-5"}

    async def test_update_config_invalid_keys_raises(self, manager):
        profile = await manager.create_profile(name="Invalid Update", area="model", config={})
        with pytest.raises(ValueError, match="not valid for area"):
            await manager.update_profile(
                profile.profile_id, config={"permission_mode": "default"}
            )

    async def test_update_nonexistent_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            await manager.update_profile("nonexistent", name="X")

    async def test_update_duplicate_name_raises(self, manager):
        await manager.create_profile(name="Existing", area="model", config={})
        p2 = await manager.create_profile(name="Rename Me", area="model", config={})
        with pytest.raises(ValueError, match="already exists"):
            await manager.update_profile(p2.profile_id, name="Existing")


@pytest.mark.asyncio
class TestProfileDelete:
    async def test_delete_profile(self, manager):
        profile = await manager.create_profile(name="Delete Me", area="model", config={})
        success = await manager.delete_profile(profile.profile_id)
        assert success is True
        assert await manager.get_profile(profile.profile_id) is None

    async def test_delete_nonexistent_returns_false(self, manager):
        result = await manager.delete_profile("does-not-exist")
        assert result is False

    async def test_delete_unguarded_no_template_manager(self, manager):
        profile = await manager.create_profile(name="Free", area="model", config={})
        # Without template_manager guard, deletion always succeeds
        success = await manager.delete_profile(profile.profile_id)
        assert success is True


@pytest.mark.asyncio
class TestProfileDeletionGuard:
    async def test_delete_blocked_when_template_references_profile(self, manager, template_manager):
        profile = await manager.create_profile(name="Locked Profile", area="model", config={})

        # Create a template referencing this profile
        await template_manager.create_template(
            name="Dependent Template",
            config=SessionConfig(permission_mode="default"),
            profile_ids={"model": profile.profile_id},
        )

        with pytest.raises(ProfileInUseError) as exc_info:
            await manager.delete_profile(profile.profile_id, template_manager=template_manager)

        err = exc_info.value
        assert profile.profile_id == err.profile_id
        assert "Dependent Template" in err.template_names

    async def test_delete_allowed_when_no_references(self, manager, template_manager):
        profile = await manager.create_profile(name="Free Profile", area="model", config={})
        # Template does NOT reference this profile
        await template_manager.create_template(
            name="Unrelated Template",
            config=SessionConfig(permission_mode="default"),
        )
        success = await manager.delete_profile(profile.profile_id, template_manager=template_manager)
        assert success is True

    async def test_delete_error_lists_all_blocking_templates(self, manager, template_manager):
        profile = await manager.create_profile(name="Multi-Use Profile", area="model", config={})
        for i in range(3):
            await template_manager.create_template(
                name=f"Template {i}",
                config=SessionConfig(permission_mode="default"),
                profile_ids={"model": profile.profile_id},
            )

        with pytest.raises(ProfileInUseError) as exc_info:
            await manager.delete_profile(profile.profile_id, template_manager=template_manager)

        assert len(exc_info.value.template_ids) == 3


# ---- ProfileManager persistence ----


@pytest.mark.asyncio
class TestProfilePersistence:
    async def test_profiles_survive_reload(self, data_dir):
        mgr1 = ProfileManager(data_dir)
        profile = await mgr1.create_profile(
            name="Persist Me",
            area="features",
            config={"history_distillation_enabled": True},
        )
        # Create a fresh manager and load from disk
        mgr2 = ProfileManager(data_dir)
        await mgr2.load_profiles()
        loaded = await mgr2.get_profile(profile.profile_id)
        assert loaded is not None
        assert loaded.name == "Persist Me"
        assert loaded.config == {"history_distillation_enabled": True}


# ---- MinionTemplate migration ----


class TestMinionTemplateMigration:
    def test_legacy_profile_id_field_dropped(self):
        """Old profile_id placeholder is silently dropped in from_dict migration."""
        data = {
            "template_id": "t1",
            "name": "Legacy",
            "permission_mode": "default",
            "profile_id": "some-old-uuid",  # Legacy field
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        template = MinionTemplate.from_dict(data)
        assert not hasattr(template, "profile_id")
        assert template.profile_ids == {}
        assert template.template_overrides == {}

    def test_profile_ids_roundtrip(self):
        """profile_ids and template_overrides survive to_dict/from_dict."""
        now = datetime.now(UTC)
        template = MinionTemplate(
            template_id="t1",
            name="Profiled",
            permission_mode="default",
            profile_ids={"model": "uuid-1", "permissions": "uuid-2"},
            template_overrides={"model": "claude-opus-4-5"},
            created_at=now,
            updated_at=now,
        )
        data = template.to_dict()
        restored = MinionTemplate.from_dict(data)
        assert restored.profile_ids == {"model": "uuid-1", "permissions": "uuid-2"}
        assert restored.template_overrides == {"model": "claude-opus-4-5"}

    def test_missing_profile_ids_defaults_to_empty(self):
        """Templates without profile_ids default to {} (not None)."""
        data = {
            "template_id": "t1",
            "name": "No Profile",
            "permission_mode": "default",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        template = MinionTemplate.from_dict(data)
        assert template.profile_ids == {}
        assert template.template_overrides == {}


# ---- TemplateManager with profile_ids ----


@pytest.mark.asyncio
class TestTemplateManagerProfileIds:
    async def test_create_template_with_profile_ids(self, template_manager):
        template = await template_manager.create_template(
            name="Profiled Template",
            config=SessionConfig(permission_mode="default"),
            profile_ids={"model": "uuid-model-profile"},
            template_overrides={"effort": "high"},
        )
        assert template.profile_ids == {"model": "uuid-model-profile"}
        assert template.template_overrides == {"effort": "high"}

    async def test_update_template_profile_ids(self, template_manager):
        template = await template_manager.create_template(
            name="Update Profile Test",
            config=SessionConfig(permission_mode="default"),
        )
        updated = await template_manager.update_template(
            template.template_id,
            profile_ids={"permissions": "uuid-perm-profile"},
        )
        assert updated.profile_ids == {"permissions": "uuid-perm-profile"}

    async def test_import_template_carries_profile_ids(self, template_manager):
        envelope = {
            "version": 1,
            "template": {
                "name": "Import Profile Test",
                "permission_mode": "default",
                "profile_ids": {"model": "uuid-model"},
                "template_overrides": {"effort": "low"},
            },
        }
        template = await template_manager.import_template(envelope)
        assert template.profile_ids == {"model": "uuid-model"}
        assert template.template_overrides == {"effort": "low"}
