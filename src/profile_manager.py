"""
Profile Manager

Manages configuration profiles with file-based storage in data/profiles/.
Profiles are the base layer in the 3-tier chain: Profile → Template → Session.

Storage format: data/profiles/{slug}.json
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .template_manager import TemplateManager

# PROFILE_AREAS is defined in config_resolution.py (single source of truth).
# Import it here for validation.
from .config_resolution import PROFILE_AREA_KEYS, PROFILE_AREAS
from .logging_config import get_logger
from .models.config_profile import ConfigProfile
from .slug_utils import slugify as _slugify

logger = logging.getLogger(__name__)
profile_logger = get_logger("profile_manager", category="PROFILE_MANAGER")

# Fields reclassified from the "isolation" area to "features" (issue #1707).
# Migrated off any pre-existing isolation-area profile at load_profiles() time.
_MIGRATED_ISOLATION_TO_FEATURES_FIELDS = ("extra_env", "max_subagent_spawn_depth")


class ProfileInUseError(Exception):
    """Raised when deleting a profile that is referenced by one or more templates."""

    def __init__(self, profile_id: str, template_ids: list[str], template_names: list[str]):
        self.profile_id = profile_id
        self.template_ids = template_ids
        self.template_names = template_names
        names_preview = ", ".join(template_names[:5])
        if len(template_names) > 5:
            names_preview += "..."
        super().__init__(
            f"Profile {profile_id} is referenced by {len(template_ids)} template(s): {names_preview}"
        )


class ProfileManager:
    """Manages configuration profiles with CRUD operations.

    Profiles are stored as JSON files in data/profiles/.
    In-memory cache is populated at startup via load_profiles().
    """

    def __init__(self, data_dir: Path):
        self.profiles_dir = data_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.profiles: dict[str, ConfigProfile] = {}
        profile_logger.debug(f"ProfileManager initialized with data_dir: {data_dir}")

    async def load_profiles(
        self,
        template_manager: "TemplateManager | None" = None,
    ) -> None:
        """Load all profiles from disk into in-memory cache.

        Args:
            template_manager: If provided, also runs the idempotent
                isolation->features field migration (issue #1707) after
                loading. Must already have its templates loaded, since the
                migration scans them for profile references.
        """
        self.profiles.clear()
        loaded = 0
        for json_file in self.profiles_dir.glob("*.json"):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                profile = ConfigProfile.from_dict(data)
                self.profiles[profile.profile_id] = profile
                loaded += 1
                profile_logger.debug(f"Loaded profile: {profile.name} ({profile.profile_id})")
            except Exception as e:
                logger.error(f"Error loading profile {json_file}: {e}")
        profile_logger.info(f"Loaded {loaded} profiles from disk")

        if template_manager is not None:
            await self._migrate_isolation_to_features_fields(template_manager)

    async def _migrate_isolation_to_features_fields(
        self,
        template_manager: "TemplateManager",
    ) -> None:
        """Idempotent migration: promote extra_env/max_subagent_spawn_depth off isolation profiles.

        Issue #1707 reclassified these two fields from the "isolation" area to
        "features". Profiles saved before that change may still carry them under
        area="isolation", which would fail validation on their next update_profile()
        call. For each such profile still on disk:
          - If referenced by one or more templates (same scan pattern as
            delete_profile()), write the migrated value(s) as an explicit override
            into each referencing template's own config. Template config always
            wins over profile config in resolve_effective_config(), so the
            resolved value is unchanged.
          - If unreferenced, auto-create a standalone, unbound Features-area
            profile containing just the migrated keys (name-collision guarded).
          - Strip the migrated keys from the isolation profile and persist it
            last. This ordering — dependent writes before the source strip — is
            what makes the pass crash-safe: if interrupted, the isolation
            profile still has the original keys on next boot and the whole pass
            safely re-runs. It's also what makes it self-terminating: absence of
            the keys on the next load means nothing to do, no sentinel needed.
        """
        templates = await template_manager.list_templates()

        for profile in list(self.profiles.values()):
            if profile.area != "isolation":
                continue

            migrated_values = {
                field: profile.config[field]
                for field in _MIGRATED_ISOLATION_TO_FEATURES_FIELDS
                if profile.config.get(field) is not None
            }
            if not migrated_values:
                continue

            # Isolate failures per-profile: one profile's migration error must not
            # crash server startup or block migration of the other profiles in this
            # pass. The unmigrated profile simply retries on the next boot.
            try:
                referencing = [
                    t for t in templates
                    if t.profile_ids and profile.profile_id in t.profile_ids.values()
                ]

                if referencing:
                    for template in referencing:
                        await template_manager.update_template(
                            template.template_id, **dict(migrated_values)
                        )
                    profile_logger.info(
                        f"Migrated fields {sorted(migrated_values.keys())} from isolation profile "
                        f"'{profile.name}' ({profile.profile_id}) to override(s) on template(s): "
                        f"{[t.name for t in referencing]}"
                    )
                else:
                    new_name = f"{profile.name} (migrated)"
                    if any(p.name == new_name for p in self.profiles.values()):
                        profile_logger.info(
                            f"Migration target profile '{new_name}' already exists; skipping "
                            f"orphan-profile creation for isolation profile '{profile.name}' "
                            f"({profile.profile_id}), stripping migrated keys only"
                        )
                    else:
                        new_profile = await self.create_profile(
                            name=new_name,
                            area="features",
                            config=dict(migrated_values),
                        )
                        profile_logger.info(
                            f"Migrated fields {sorted(migrated_values.keys())} from orphan isolation "
                            f"profile '{profile.name}' ({profile.profile_id}) to new unbound Features "
                            f"profile '{new_profile.name}' ({new_profile.profile_id})"
                        )

                remaining_config = {
                    k: v for k, v in profile.config.items()
                    if k not in _MIGRATED_ISOLATION_TO_FEATURES_FIELDS
                }
                await self.update_profile(profile.profile_id, config=remaining_config)
            except Exception as e:
                logger.error(
                    f"Error migrating isolation profile '{profile.name}' "
                    f"({profile.profile_id}) to features: {e}"
                )

    async def create_profile(
        self,
        name: str,
        area: str,
        config: dict[str, Any],
    ) -> ConfigProfile:
        """Create a new configuration profile.

        Args:
            name: Unique display name.
            area: One of PROFILE_AREAS keys.
            config: Config values; all keys must belong to the given area.

        Raises:
            ValueError: On invalid area, invalid config keys, or duplicate name.
        """
        if not name or not name.strip():
            raise ValueError("Profile name cannot be empty")

        if area not in PROFILE_AREA_KEYS:
            raise ValueError(f"Invalid area '{area}'. Must be one of: {', '.join(sorted(PROFILE_AREA_KEYS))}")

        if any(p.name == name.strip() for p in self.profiles.values()):
            raise ValueError(f"Profile with name '{name}' already exists")

        invalid_keys = set(config.keys()) - PROFILE_AREAS[area]
        if invalid_keys:
            raise ValueError(
                f"Config keys {invalid_keys} are not valid for area '{area}'. "
                f"Valid keys: {PROFILE_AREAS[area]}"
            )

        now = datetime.now(UTC)
        profile = ConfigProfile(
            profile_id=str(uuid.uuid4()),
            name=name.strip(),
            area=area,
            config=config,
            created_at=now,
            updated_at=now,
        )

        await self._save_profile(profile)
        self.profiles[profile.profile_id] = profile
        profile_logger.info(f"Created profile: {profile.name} ({profile.profile_id})")
        return profile

    async def get_profile(self, profile_id: str) -> ConfigProfile | None:
        """Get profile by ID."""
        return self.profiles.get(profile_id)

    async def list_profiles(self, area: str | None = None) -> list[ConfigProfile]:
        """List profiles, optionally filtered by area."""
        profiles = list(self.profiles.values())
        if area is not None:
            profiles = [p for p in profiles if p.area == area]
        return profiles

    async def update_profile(
        self,
        profile_id: str,
        name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> ConfigProfile:
        """Partially update a profile.

        Args:
            profile_id: Profile to update.
            name: New display name (must be unique).
            config: New config dict (replaces existing config); all keys must belong to area.

        Raises:
            ValueError: If profile not found, name conflict, or invalid config keys.
        """
        profile = self.profiles.get(profile_id)
        if not profile:
            raise ValueError(f"Profile {profile_id} not found")

        if name is not None and name.strip() != profile.name:
            name = name.strip()
            if any(p.name == name for p in self.profiles.values()):
                raise ValueError(f"Profile with name '{name}' already exists")
            profile.name = name

        if config is not None:
            invalid_keys = set(config.keys()) - PROFILE_AREAS[profile.area]
            if invalid_keys:
                raise ValueError(
                    f"Config keys {invalid_keys} are not valid for area '{profile.area}'. "
                    f"Valid keys: {PROFILE_AREAS[profile.area]}"
                )
            profile.config = config

        profile.updated_at = datetime.now(UTC)
        await self._save_profile(profile)
        profile_logger.info(f"Updated profile: {profile.name} ({profile_id})")
        return profile

    async def delete_profile(
        self,
        profile_id: str,
        template_manager: "TemplateManager | None" = None,
    ) -> bool:
        """Delete a profile.

        Raises ProfileInUseError if template_manager is provided and templates
        reference this profile via profile_ids.

        Args:
            profile_id: Profile to delete.
            template_manager: If provided, check for referencing templates first.
        """
        if profile_id not in self.profiles:
            return False

        if template_manager:
            templates = await template_manager.list_templates()
            blocking = [
                t for t in templates
                if t.profile_ids and profile_id in t.profile_ids.values()
            ]
            if blocking:
                raise ProfileInUseError(
                    profile_id=profile_id,
                    template_ids=[t.template_id for t in blocking],
                    template_names=[t.name for t in blocking],
                )

        profile = self.profiles[profile_id]
        slug = _slugify(profile.name)
        json_file = self.profiles_dir / f"{slug}.json"
        if json_file.exists():
            json_file.unlink()

        del self.profiles[profile_id]
        profile_logger.info(f"Deleted profile: {profile.name} ({profile_id})")
        return True

    async def _save_profile(self, profile: ConfigProfile) -> None:
        """Persist profile to disk as {slug}.json."""
        slug = _slugify(profile.name)
        json_file = self.profiles_dir / f"{slug}.json"
        with open(json_file, "w") as f:
            json.dump(profile.to_dict(), f, indent=2)
        profile_logger.debug(f"Saved profile to disk: {json_file}")
