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
    role: str | None = None
    system_prompt: str | None = None
    description: str | None = None
    model: str | None = None
    capabilities: list[str] | None = None
    override_system_prompt: bool = False
    sandbox_enabled: bool = False
    sandbox_config: dict | None = None
    cli_path: str | None = None  # Custom CLI executable path (issue #489)
    additional_directories: list[str] | None = None  # Extra dirs for agent access (issue #630)
    # Docker session isolation (issue #496)
    docker_enabled: bool = False
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    # Thinking and effort configuration (issue #580)
    thinking_mode: str | None = None
    thinking_budget_tokens: int | None = None
    effort: str | None = None
    # History distillation toggle (issue #710, renamed #736)
    history_distillation_enabled: bool = True
    # Auto-memory mode (issue #709, replaces #708 disable_auto_memory boolean)
    auto_memory_mode: str = "claude"  # "claude" | "session" | "disabled"
    # Skill creating toggle (issue #749)
    skill_creating_enabled: bool = False
    # MCP server configuration (issue #676)
    mcp_server_ids: list[str] | None = None
    # MCP toggle configuration (issue #786)
    enable_claudeai_mcp_servers: bool = True
    strict_mcp_config: bool = False
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
        if self.additional_directories is None:
            self.additional_directories = []
        if self.docker_extra_mounts is None:
            self.docker_extra_mounts = []
        if self.mcp_server_ids is None:
            self.mcp_server_ids = []

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
        # Backward-compat defaults for fields added after initial schema
        data.setdefault('disallowed_tools', None)
        data.setdefault('override_system_prompt', False)
        data.setdefault('sandbox_enabled', False)
        data.setdefault('sandbox_config', None)
        data.setdefault('cli_path', None)
        data.setdefault('additional_directories', None)
        data.setdefault('docker_enabled', False)
        data.setdefault('docker_image', None)
        data.setdefault('docker_extra_mounts', None)
        data.setdefault('thinking_mode', None)
        data.setdefault('thinking_budget_tokens', None)
        data.setdefault('effort', None)
        if data.get('effort') == 'max':
            data['effort'] = 'high'
        # Migrate knowledge_management_enabled → history_distillation_enabled (issue #736)
        if 'knowledge_management_enabled' in data and 'history_distillation_enabled' not in data:
            data['history_distillation_enabled'] = data.pop('knowledge_management_enabled')
        else:
            data.pop('knowledge_management_enabled', None)
            data.setdefault('history_distillation_enabled', True)
        # Migrate legacy disable_auto_memory boolean to auto_memory_mode enum (issue #709)
        if 'disable_auto_memory' in data and 'auto_memory_mode' not in data:
            data['auto_memory_mode'] = "disabled" if data.pop('disable_auto_memory') else "claude"
        else:
            data.pop('disable_auto_memory', None)
            data.setdefault('auto_memory_mode', 'claude')
        data.setdefault('skill_creating_enabled', False)
        data.setdefault('mcp_server_ids', None)
        # MCP toggle configuration (issue #786)
        data.setdefault('enable_claudeai_mcp_servers', True)
        data.setdefault('strict_mcp_config', False)
        # Backward-compat renames (issue #731)
        if 'default_role' in data:
            data.setdefault('role', data.pop('default_role'))
        if 'default_system_prompt' in data:
            data.setdefault('system_prompt', data.pop('default_system_prompt'))
        return cls(**data)
