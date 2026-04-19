"""
ConfigProfile Data Model

Defines reusable configuration profiles scoped to one of 6 settings areas.
Profiles form the base layer in the 3-tier chain: Profile → Template → Session.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Fields that must be lists (may be stored as comma-separated strings by older UI)
_LIST_CONFIG_KEYS = {"docker_proxy_allowlist_domains", "docker_proxy_credential_names"}


@dataclass
class ConfigProfile:
    """
    Reusable configuration profile scoped to a single settings area.

    Profiles hold a subset of config fields belonging to one area (e.g. "model",
    "permissions"). Templates reference profiles by area via profile_ids, inheriting
    the profile values unless overridden at the template or session level.
    """

    profile_id: str          # UUID
    name: str                # Display name (unique)
    area: str                # One of PROFILE_AREAS keys
    config: dict[str, Any]   # Only fields valid for this area
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "area": self.area,
            "config": self.config,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigProfile":
        """Create from dictionary loaded from JSON."""
        config = data.get("config", {})
        # Normalize list fields that may have been saved as comma-separated strings
        for key in _LIST_CONFIG_KEYS:
            if key in config and isinstance(config[key], str):
                val = config[key]
                config[key] = [s.strip() for s in val.split(",") if s.strip()] if val.strip() else None
        return cls(
            profile_id=data["profile_id"],
            name=data["name"],
            area=data["area"],
            config=config,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
