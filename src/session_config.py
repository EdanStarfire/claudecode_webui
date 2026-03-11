"""
SessionConfig - Bundled configuration for session/minion/template creation.

Groups configuration toggle parameters that were previously threaded
individually through create_session/create_template/create_minion call chains.
Identity and lifecycle params (session_id, name, role, etc.) remain as
direct function parameters.

Issue #713: Reduce parameter sprawl across session creation APIs.
"""

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class SessionConfig:
    """Bundled configuration for session/minion/template creation.

    Groups configuration toggle parameters that were previously threaded
    individually through create_session/create_template/create_minion call chains.
    Identity and lifecycle params (session_id, name, role, etc.) remain as
    direct function parameters.
    """

    # Permission
    permission_mode: str = "acceptEdits"

    # Prompt
    system_prompt: str | None = None
    override_system_prompt: bool = False

    # Tools
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None

    # Model
    model: str | None = None
    thinking_mode: str | None = None
    thinking_budget_tokens: int | None = None
    effort: str | None = None

    # Environment
    working_directory: str | None = None
    additional_directories: list[str] | None = None
    cli_path: str | None = None
    setting_sources: list[str] | None = None

    # Sandbox
    sandbox_enabled: bool = False
    sandbox_config: dict | None = None

    # Docker
    docker_enabled: bool = False
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None

    # Features
    knowledge_management_enabled: bool = True
    auto_memory_mode: str = "claude"  # "claude" | "session" | "disabled"


class SessionConfigBase(BaseModel):
    """Shared Pydantic base for session/minion/template request models.

    All request models that create sessions, minions, or templates inherit
    from this base to avoid duplicating the common config fields.
    """

    permission_mode: str = "acceptEdits"
    system_prompt: str | None = None
    override_system_prompt: bool = False
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    cli_path: str | None = None
    additional_directories: list[str] | None = None
    setting_sources: list[str] | None = None
    sandbox_enabled: bool = False
    sandbox_config: dict | None = None
    docker_enabled: bool = False
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    thinking_mode: str | None = None
    thinking_budget_tokens: int | None = None
    effort: str | None = None
    knowledge_management_enabled: bool = True
    auto_memory_mode: str = "claude"

    def to_session_config(self, **overrides) -> SessionConfig:
        """Convert to SessionConfig dataclass, with optional field overrides."""
        data = {
            "permission_mode": self.permission_mode,
            "system_prompt": self.system_prompt,
            "override_system_prompt": self.override_system_prompt,
            "allowed_tools": self.allowed_tools,
            "disallowed_tools": self.disallowed_tools,
            "model": self.model,
            "cli_path": self.cli_path,
            "additional_directories": self.additional_directories,
            "setting_sources": self.setting_sources,
            "sandbox_enabled": self.sandbox_enabled,
            "sandbox_config": self.sandbox_config,
            "docker_enabled": self.docker_enabled,
            "docker_image": self.docker_image,
            "docker_extra_mounts": self.docker_extra_mounts,
            "thinking_mode": self.thinking_mode,
            "thinking_budget_tokens": self.thinking_budget_tokens,
            "effort": self.effort,
            "knowledge_management_enabled": self.knowledge_management_enabled,
            "auto_memory_mode": self.auto_memory_mode,
        }
        data.update(overrides)
        return SessionConfig(**data)
