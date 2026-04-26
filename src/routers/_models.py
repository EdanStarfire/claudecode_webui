"""
Pydantic request models for all API endpoints.

Moved from src/web_server.py to centralize model definitions.
"""

from pydantic import BaseModel, Field

from ..mcp_config_manager import McpServerType
from ..session_config import SessionConfig


class ProjectCreateRequest(BaseModel):
    name: str
    working_directory: str
    max_concurrent_minions: int | None = None  # Max concurrent minions per project (None = use global default)

    class Config:
        # Silently ignore unknown fields like is_multi_agent for backward compatibility
        extra = "ignore"


class PermissionPreviewRequest(BaseModel):
    """Request to preview effective permissions (issue #36)"""
    working_directory: str
    setting_sources: list[str] | None = None  # Default: ["user", "project", "local"]
    session_allowed_tools: list[str] | None = None  # Session-level allowed tools


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    is_expanded: bool | None = None
    max_concurrent_minions: int | None = Field(None, ge=1)


class ProjectReorderRequest(BaseModel):
    project_ids: list[str]


class SessionCreateRequest(SessionConfig):
    project_id: str
    name: str | None = None


class MessageRequest(BaseModel):
    message: str
    metadata: dict | None = None


class SessionNameUpdateRequest(BaseModel):
    name: str


class SessionUpdateRequest(BaseModel):
    """Generic session update request - all fields optional"""
    name: str | None = None
    model: str | None = None  # sonnet, opus, haiku, opusplan
    allowed_tools: list[str] | None = None  # List of tool names to allow
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    role: str | None = None
    system_prompt: str | None = None
    override_system_prompt: bool | None = None
    capabilities: list[str] | None = None
    sandbox_enabled: bool | None = None
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load
    cli_path: str | None = None  # Issue #489: custom CLI executable path
    additional_directories: list[str] | None = None  # Issue #630: extra dirs for agent access
    # Docker sub-field updates (issue #787): docker_enabled is immutable; image/mounts/home are editable
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    docker_home_directory: str | None = None
    # Thinking and effort configuration (issue #540)
    thinking_mode: str | None = None  # "adaptive", "enabled", "disabled"
    thinking_budget_tokens: int | None = None  # Token budget (min 1024)
    effort: str | None = None  # "low", "medium", "high"
    # History distillation toggle (issue #710, renamed #736)
    history_distillation_enabled: bool | None = None
    # Auto-memory mode (issue #709)
    auto_memory_mode: str | None = None  # "claude" | "session" | "disabled"
    # Custom directory for auto-memory when mode is "claude" (issue #906)
    auto_memory_directory: str | None = None
    # Skill creating toggle (issue #749)
    skill_creating_enabled: bool | None = None
    # MCP server configuration (issue #676)
    mcp_server_ids: list[str] | None = None
    enable_claudeai_mcp_servers: bool | None = None
    strict_mcp_config: bool | None = None
    bare_mode: bool | None = None


class SessionReorderRequest(BaseModel):
    session_ids: list[str]


class PermissionModeRequest(BaseModel):
    mode: str


class McpToggleRequest(BaseModel):
    name: str
    enabled: bool


class McpReconnectRequest(BaseModel):
    name: str


class CommSendRequest(BaseModel):
    to_minion_id: str | None = None
    to_user: bool = False
    content: str
    comm_type: str = "task"


class MinionCreateRequest(SessionConfig):
    name: str
    role: str | None = ""
    system_prompt: str | None = ""
    capabilities: list[str] | None = None
    working_directory: str | None = None  # Optional custom working directory for this minion
    permission_mode: str = "default"  # Override default from SessionConfig


class ScheduleCreateRequest(BaseModel):
    """Request to create a cron schedule (issue #495, #578)."""
    minion_id: str | None = None  # Optional for ephemeral schedules (issue #578)
    name: str
    cron_expression: str
    prompt: str
    reset_session: bool = False
    max_retries: int = 3
    timeout_seconds: int = 3600
    session_config: dict | None = None  # Ephemeral session configuration (issue #578)


class ScheduleUpdateRequest(BaseModel):
    """Request to update a schedule (issue #495, #578)."""
    name: str | None = None
    cron_expression: str | None = None
    prompt: str | None = None
    max_retries: int | None = None
    timeout_seconds: int | None = None
    session_config: dict | None = None  # Ephemeral session configuration (issue #578)
    sandbox_enabled: bool = False  # Enable OS-level sandboxing (issue #319)
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load


class TemplateCreateRequest(SessionConfig):
    name: str
    role: str | None = None
    system_prompt: str | None = None
    description: str | None = None
    capabilities: list[str] | None = None
    # Composable profiles (issue #1062)
    profile_ids: dict[str, str] | None = None
    template_overrides: dict | None = None


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    role: str | None = None
    system_prompt: str | None = None
    description: str | None = None
    model: str | None = None
    capabilities: list[str] | None = None
    override_system_prompt: bool | None = None
    sandbox_enabled: bool | None = None
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    cli_path: str | None = None  # Issue #489: custom CLI path
    additional_directories: list[str] | None = None  # Issue #630
    # Docker session isolation (issue #496)
    docker_enabled: bool | None = None
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    # Docker proxy configuration (issue #1116)
    docker_home_directory: str | None = None
    docker_proxy_enabled: bool | None = None
    docker_proxy_image: str | None = None
    assigned_secrets: list[str] | None = None
    docker_proxy_allowlist_domains: list[str] | None = None
    # Thinking and effort configuration (issue #580)
    thinking_mode: str | None = None
    thinking_budget_tokens: int | None = None
    effort: str | None = None
    # History distillation toggle (issue #710, renamed #736)
    history_distillation_enabled: bool | None = None
    # Auto-memory mode (issue #709)
    auto_memory_mode: str | None = None  # "claude" | "session" | "disabled"
    # Custom directory for auto-memory when mode is "claude" (issue #906)
    auto_memory_directory: str | None = None
    # Skill creating toggle (issue #749)
    skill_creating_enabled: bool | None = None
    # MCP server configuration (issue #676)
    mcp_server_ids: list[str] | None = None
    # MCP toggle configuration (issue #786)
    enable_claudeai_mcp_servers: bool | None = None
    strict_mcp_config: bool | None = None
    # Runtime feature flags (issue #1116)
    setting_sources: list[str] | None = None
    bare_mode: bool | None = None
    env_scrub_enabled: bool | None = None
    # Composable profiles (issue #1062)
    profile_ids: dict[str, str] | None = None
    template_overrides: dict | None = None


# Profile request models (issue #1062)
class ProfileCreateRequest(BaseModel):
    name: str
    area: str
    config: dict


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    config: dict | None = None


# MCP config request models (issue #676)
class McpConfigCreateRequest(BaseModel):
    name: str
    type: McpServerType
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool = True
    oauth_enabled: bool = False
    oauth_client_id: str | None = None
    oauth_callback_port: int | None = None


class McpConfigUpdateRequest(BaseModel):
    name: str | None = None
    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    oauth_enabled: bool | None = None
    oauth_client_id: str | None = None
    oauth_callback_port: int | None = None


class McpOAuthInitiateRequest(BaseModel):
    redirect_uri: str


class McpConfigExportRequest(BaseModel):
    ids: list[str] | None = None  # If None, export all


class McpConfigImportRequest(BaseModel):
    servers: dict[str, dict]  # Named dict: {serverName: {type, command, ...}}
    dry_run: bool = True


# Proxy credential request models (issue #1053) — kept for backward compat during transition
class CredentialCreateRequest(BaseModel):
    name: str
    host_pattern: str
    header_name: str = "Authorization"
    value_format: str = "Bearer {value}"
    real_value: str
    delivery: dict


# Secrets request models (issue #827, extended by #1134)
class SecretCreateRequest(BaseModel):
    """Request to create a new secret (full schema + value)."""
    name: str
    type: str = "generic"
    target_hosts: list[str]
    value: str  # Write-only: stored in keyring, never returned
    inject_env: str | None = None
    inject_file: dict | None = None  # InjectFileSpec shape
    scrub: dict | None = None  # ScrubSpec shape
    # Issue #1134: typed-secret fields
    username: str | None = None      # basic_auth only: plaintext username metadata
    injection: dict | None = None    # api_key only: InjectionSpec shape
    refresh: dict | None = None      # oauth2 only: RefreshSpec shape


class SecretUpdateRequest(BaseModel):
    """Request to update secret metadata and/or value (all fields optional)."""
    type: str | None = None
    target_hosts: list[str] | None = None
    value: str | None = None  # Write-only: if provided, replaces existing value
    inject_env: str | None = None
    inject_file: dict | None = None
    scrub: dict | None = None
    # Issue #1134: typed-secret fields
    username: str | None = None
    injection: dict | None = None
    refresh: dict | None = None


class PermissionResponseRequest(BaseModel):
    decision: str
    apply_suggestions: bool = False
    clarification_message: str | None = None
    selected_suggestions: list | None = None
    updated_input: dict | None = None


def _validate_additional_directories(dirs: list[str] | None, working_directory: str | None) -> list[str]:
    """Validate additional directories: absolute paths, no duplicates, not same as working_dir."""
    import os
    if not dirs:
        return []
    validated = []
    seen = set()
    for d in dirs:
        d = d.strip()
        if not d:
            continue
        if not os.path.isabs(d):
            raise ValueError(f"Directory must be an absolute path: {d}")
        normalized = os.path.normpath(d)
        if normalized in seen:
            continue
        if working_directory and normalized == os.path.normpath(working_directory):
            continue
        seen.add(normalized)
        validated.append(normalized)
    return validated
