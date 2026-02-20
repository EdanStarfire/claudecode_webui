"""
Minion Template Data Model

Defines reusable configuration templates for minion creation with
pre-configured permissions, tools, and default settings.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class MinionTemplate:
    """
    Reusable configuration template for minion creation.

    Stores permission mode, allowed tools, and default settings
    that can be applied when creating new minions.
    """
    template_id: str
    name: str
    permission_mode: str  # default, acceptEdits, plan, bypassPermissions
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None  # Tools explicitly denied (issue #461)
    default_role: str | None = None
    default_system_prompt: str | None = None
    description: str | None = None
    model: str | None = None
    capabilities: list[str] | None = None
    override_system_prompt: bool = False
    sandbox_enabled: bool = False
    sandbox_config: dict | None = None
    cli_path: str | None = None  # Custom CLI executable path (issue #489)
    # Docker session isolation (issue #496)
    docker_enabled: bool = False
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self):
        """Initialize timestamps and ensure list fields are lists."""
        if self.created_at is None:
            self.created_at = datetime.now(UTC)
        if self.updated_at is None:
            self.updated_at = datetime.now(UTC)
        if self.allowed_tools is None:
            self.allowed_tools = []
        if self.disallowed_tools is None:
            self.disallowed_tools = []
        if self.capabilities is None:
            self.capabilities = []
        if self.docker_extra_mounts is None:
            self.docker_extra_mounts = []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'MinionTemplate':
        """Create from dictionary loaded from JSON."""
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        return cls(**data)
