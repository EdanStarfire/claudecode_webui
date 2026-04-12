"""
SessionConfig - Bundled configuration for session/minion/template creation.

Groups configuration toggle parameters that were previously threaded
individually through create_session/create_template/create_minion call chains.
Identity and lifecycle params (session_id, name, role, etc.) remain as
direct function parameters.

Issue #713: Reduce parameter sprawl across session creation APIs.
"""

from pydantic import BaseModel


class SessionConfig(BaseModel):
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
    docker_home_directory: str | None = None
    # Issue #1049: Proxy mode (sidecar model)
    docker_proxy_container: str | None = None
    docker_proxy_ca_cert: str | None = None

    # Features
    history_distillation_enabled: bool = True
    auto_memory_mode: str = "claude"  # "claude" | "session" | "disabled"
    auto_memory_directory: str | None = None  # Custom directory for auto-memory when mode is "claude" (issue #906)
    skill_creating_enabled: bool = False

    # MCP servers (issue #676)
    mcp_server_ids: list[str] | None = None  # Global MCP config IDs to attach
    enable_claudeai_mcp_servers: bool = True  # Toggle ENABLE_CLAUDEAI_MCP_SERVERS env var
    strict_mcp_config: bool = False  # Pass --strict-mcp-config to disable local .mcp.json
    bare_mode: bool = False  # Pass --bare to skip hooks, LSP, plugin sync, skill walks
    env_scrub_enabled: bool = False  # Issue #957: Strip credentials from subprocess envs
