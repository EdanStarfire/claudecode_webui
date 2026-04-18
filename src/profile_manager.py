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

    async def load_profiles(self) -> None:
        """Load all profiles from disk into in-memory cache."""
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
