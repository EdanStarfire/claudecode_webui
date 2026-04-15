"""
Config Resolution — Issue #1059 Phase 2

Resolves effective SessionConfig by merging template defaults with
session-level overrides. Called at session start/restart so config
changes apply on the next restart (not live to running sessions).
"""

from .session_config import SessionConfig
from .session_manager import SessionInfo
from .template_manager import TemplateManager

# Fields that exist on both MinionTemplate and SessionConfig (the mergeable set).
# Excludes identity fields (template_id, name, role, description, capabilities, profile_id),
# lifecycle fields (created_at, updated_at), and session-only fields (working_directory).
# Fields present in CONFIG_FIELDS but absent on MinionTemplate are silently skipped via
# hasattr() in resolve_effective_config() — this allows SessionConfig-only fields to be
# listed here for completeness without causing errors.
CONFIG_FIELDS: set[str] = {
    "permission_mode", "system_prompt", "override_system_prompt",
    "allowed_tools", "disallowed_tools", "model",
    "thinking_mode", "thinking_budget_tokens", "effort",
    "additional_directories", "cli_path", "setting_sources",
    "sandbox_enabled", "sandbox_config",
    "docker_enabled", "docker_image", "docker_extra_mounts",
    "docker_home_directory", "docker_proxy_enabled", "docker_proxy_image",
    "docker_proxy_credentials",
    "history_distillation_enabled", "auto_memory_mode", "auto_memory_directory",
    "skill_creating_enabled",
    "mcp_server_ids", "enable_claudeai_mcp_servers", "strict_mcp_config",
    "bare_mode", "env_scrub_enabled",
}


async def resolve_effective_config(
    session_info: SessionInfo,
    template_manager: TemplateManager,
) -> SessionConfig:
    """Build effective SessionConfig from template + session overrides.

    Priority (high to low): session_overrides > template > session flat fields (legacy).

    Called in start_session() — config takes effect at the next session start/restart.
    Template updates between restarts are invisible to running sessions.
    """
    # Legacy path: no template linked — build from flat session fields (identical to pre-#1059)
    if not session_info.template_id:
        return _config_from_session_info(session_info)

    # Template path: fetch template, build base config, apply overrides
    template = await template_manager.get_template(session_info.template_id)
    if template is None:
        # Template was deleted after session creation — fall back to flat fields
        return _config_from_session_info(session_info)

    # Start from template values (only fields that exist on the template)
    config_data: dict = {}
    for field in CONFIG_FIELDS:
        if hasattr(template, field):
            config_data[field] = getattr(template, field)

    # Apply session_overrides on top (highest priority)
    overrides = session_info.session_overrides or {}
    for field, value in overrides.items():
        if field in CONFIG_FIELDS:
            config_data[field] = value

    # Carry template_id through for downstream reference
    config_data["template_id"] = session_info.template_id

    return SessionConfig(**config_data)


def _config_from_session_info(session_info: SessionInfo) -> SessionConfig:
    """Legacy path: build SessionConfig from flat SessionInfo fields (pre-#1059 behavior)."""
    return SessionConfig(
        permission_mode=session_info.current_permission_mode,
        system_prompt=session_info.system_prompt,
        override_system_prompt=session_info.override_system_prompt,
        allowed_tools=session_info.allowed_tools,
        disallowed_tools=session_info.disallowed_tools,
        model=session_info.model,
        additional_directories=session_info.additional_directories,
        cli_path=session_info.cli_path,
        setting_sources=session_info.setting_sources,
        sandbox_enabled=session_info.sandbox_enabled,
        sandbox_config=session_info.sandbox_config,
        docker_enabled=session_info.docker_enabled,
        docker_image=session_info.docker_image,
        docker_extra_mounts=session_info.docker_extra_mounts,
        docker_home_directory=session_info.docker_home_directory,
        docker_proxy_enabled=session_info.docker_proxy_enabled,
        docker_proxy_image=session_info.docker_proxy_image,
        docker_proxy_credentials=session_info.docker_proxy_credentials,
        thinking_mode=session_info.thinking_mode,
        thinking_budget_tokens=session_info.thinking_budget_tokens,
        effort=session_info.effort,
        history_distillation_enabled=session_info.history_distillation_enabled,
        auto_memory_mode=session_info.auto_memory_mode,
        auto_memory_directory=session_info.auto_memory_directory,
        skill_creating_enabled=session_info.skill_creating_enabled,
        mcp_server_ids=session_info.mcp_server_ids,
        enable_claudeai_mcp_servers=session_info.enable_claudeai_mcp_servers,
        strict_mcp_config=session_info.strict_mcp_config,
        bare_mode=session_info.bare_mode,
        env_scrub_enabled=session_info.env_scrub_enabled,
        template_id=session_info.template_id,
    )
