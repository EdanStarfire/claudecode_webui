"""
Minion Template Data Model

Defines reusable configuration templates for minion creation.
Configuration values (permissions, tools, model, etc.) are stored in a single
`config` dict, which is the mergeable layer in the 3-tier resolution chain:
  profile.config → template.config → session.config
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class MinionTemplate:
    """
    Reusable configuration template for minion creation.

    Identity/lifecycle fields stay flat. All CONFIG_FIELDS (permission_mode,
    allowed_tools, model, docker_*, sandbox_*, etc.) live in `config`.
    The companion .md file mechanism writes/reads config["system_prompt"].
    """
    template_id: str
    name: str
    role: str | None = None
    description: str | None = None
    capabilities: list[str] = field(default_factory=list)
    profile_ids: dict[str, str] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    watchdog: dict[str, Any] | None = None
    is_default: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(UTC)
        if self.updated_at is None:
            self.updated_at = datetime.now(UTC)
        if self.capabilities is None:
            self.capabilities = []
        if self.profile_ids is None:
            self.profile_ids = {}
        if self.config is None:
            self.config = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "capabilities": self.capabilities,
            "config": dict(self.config),
            "created_at": self.created_at.isoformat(),
            "description": self.description,
            "is_default": self.is_default,
            "name": self.name,
            "profile_ids": self.profile_ids,
            "role": self.role,
            "template_id": self.template_id,
            "updated_at": self.updated_at.isoformat(),
            "watchdog": self.watchdog,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MinionTemplate":
        """Create from dictionary loaded from JSON.

        Expects the post-#1230 shape with a top-level ``config`` dict.
        Legacy (flat-field) data must be migrated by ``_migrate_template_to_config_dict``
        in template_manager.py before this method is called.
        """
        data = dict(data)
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])

        # Non-CONFIG_FIELD backward-compat renames (safe to keep here)
        if "default_role" in data:
            data.setdefault("role", data.pop("default_role"))
        else:
            data.pop("default_role", None)

        data.pop("profile_id", None)  # legacy unused placeholder

        data.setdefault("role", None)
        data.setdefault("description", None)
        data.setdefault("capabilities", None)
        data.setdefault("profile_ids", None)
        data.setdefault("watchdog", None)
        data.setdefault("config", {})

        data.setdefault("is_default", False)

        # Silently drop unknown top-level keys (forward compat + leftover legacy fields
        # that were not fully migrated — migration should have handled them).
        known = {
            "template_id", "name", "role", "description", "capabilities",
            "profile_ids", "config", "watchdog", "is_default", "created_at", "updated_at",
        }
        for k in list(data.keys()):
            if k not in known:
                data.pop(k)

        return cls(**data)
