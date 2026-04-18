"""
Config Resolution

Resolves effective SessionConfig by merging profile defaults, template overrides,
and session-level overrides. Called at session start/restart so config changes
apply on the next restart (not live to running sessions).

3-tier resolution (high to low priority):
  session_overrides > template_overrides > profile values > template flat fields > defaults
"""

from typing import TYPE_CHECKING

from .session_config import SessionConfig
from .session_manager import SessionInfo
from .template_manager import TemplateManager

if TYPE_CHECKING:
    from .profile_manager import ProfileManager

# Fields that exist on both MinionTemplate and SessionConfig (the mergeable set).
# Excludes identity fields (template_id, name, role, description, capabilities,
# profile_ids, template_overrides), lifecycle fields (created_at, updated_at),
# and session-only fields (working_directory).
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

# 6 config areas — every field in CONFIG_FIELDS belongs to exactly one area.
PROFILE_AREAS: dict[str, set[str]] = {
    "model": {
        "model", "thinking_mode", "thinking_budget_tokens", "effort",
    },
    "permissions": {
        "permission_mode", "allowed_tools", "disallowed_tools",
        "additional_directories", "setting_sources",
    },
    "system_prompt": {
        "system_prompt", "override_system_prompt",
    },
    "mcp": {
        "mcp_server_ids", "enable_claudeai_mcp_servers", "strict_mcp_config",
    },
    "isolation": {
        "cli_path", "sandbox_enabled", "sandbox_config",
        "docker_enabled", "docker_image", "docker_extra_mounts",
        "docker_home_directory", "docker_proxy_enabled", "docker_proxy_image",
        "docker_proxy_credentials", "bare_mode", "env_scrub_enabled",
    },
    "features": {
        "history_distillation_enabled", "auto_memory_mode", "auto_memory_directory",
        "skill_creating_enabled",
    },
}

# Reverse lookup: field -> area name
FIELD_TO_AREA: dict[str, str] = {
    field: area
    for area, fields in PROFILE_AREAS.items()
    for field in fields
}

# Structural integrity check: union of all areas must exactly equal CONFIG_FIELDS.
assert set(FIELD_TO_AREA.keys()) == CONFIG_FIELDS, (
    f"PROFILE_AREAS union does not match CONFIG_FIELDS. "
    f"Missing from areas: {CONFIG_FIELDS - set(FIELD_TO_AREA.keys())}. "
    f"Extra in areas: {set(FIELD_TO_AREA.keys()) - CONFIG_FIELDS}"
)


async def resolve_effective_config(
    session_info: SessionInfo,
    template_manager: TemplateManager,
    profile_manager: "ProfileManager | None" = None,
) -> SessionConfig:
    """Build effective SessionConfig from profile + template overrides + session overrides.

    Priority (high to low):
      session_overrides > template_overrides > profile values > template flat fields > defaults

    Called in start_session() — config takes effect at the next session start/restart.
    Template updates between restarts are invisible to running sessions.

    Args:
        session_info: Session state (template_id, session_overrides, flat fields).
        template_manager: Used to fetch the linked template.
        profile_manager: Optional; if provided enables 3-tier profile resolution.
    """
    # Legacy path: no template linked — build from flat session fields (identical to pre-#1059)
    if not session_info.template_id:
        return _config_from_session_info(session_info)

    # Template path: fetch template, build base config, apply overrides
    template = await template_manager.get_template(session_info.template_id)
    if template is None:
        # Template was deleted after session creation — fall back to flat fields
        return _config_from_session_info(session_info)

    # --- 3-tier resolution (issue #1062) ---
    # Cache profile lookups within a single resolution call (load each profile once).
    profile_cache: dict[str, object] = {}

    async def _get_profile(profile_id: str):
        """Fetch profile from cache or profile_manager."""
        if profile_id not in profile_cache:
            if profile_manager:
                profile_cache[profile_id] = await profile_manager.get_profile(profile_id)
            else:
                profile_cache[profile_id] = None
        return profile_cache[profile_id]

    config_data: dict = {}
    template_profile_ids = template.profile_ids or {}
    template_overrides_dict = template.template_overrides or {}
    session_overrides = session_info.session_overrides or {}

    for field in CONFIG_FIELDS:
        # Step 1: Base value from template flat field (backward compat)
        if hasattr(template, field):
            config_data[field] = getattr(template, field)

        # Step 2: If template has a profile assigned for this field's area, use profile value
        area = FIELD_TO_AREA.get(field)
        if area and area in template_profile_ids and profile_manager:
            profile = await _get_profile(template_profile_ids[area])
            if profile is not None and field in profile.config:
                config_data[field] = profile.config[field]

        # Step 3: template_overrides win over profile values
        if field in template_overrides_dict:
            config_data[field] = template_overrides_dict[field]

        # Step 4: session_overrides win over everything (highest priority)
        if field in session_overrides:
            config_data[field] = session_overrides[field]

    # Carry template_id through for downstream reference
    config_data["template_id"] = session_info.template_id

    return SessionConfig(**config_data)


async def resolve_template_config(
    template,
    profile_manager: "ProfileManager | None" = None,
) -> dict:
    """Resolve template + profile values without session overrides.

    Returns a dict of config field -> value for use in minion spawn path.
    Shared helper between session-start and minion-spawn code paths.

    Priority (high to low): template_overrides > profile values > template flat fields
    """
    profile_cache: dict[str, object] = {}

    async def _get_profile(profile_id: str):
        if profile_id not in profile_cache:
            if profile_manager:
                profile_cache[profile_id] = await profile_manager.get_profile(profile_id)
            else:
                profile_cache[profile_id] = None
        return profile_cache[profile_id]

    config_data: dict = {}
    template_profile_ids = template.profile_ids or {}
    template_overrides_dict = template.template_overrides or {}

    for field in CONFIG_FIELDS:
        if hasattr(template, field):
            config_data[field] = getattr(template, field)

        area = FIELD_TO_AREA.get(field)
        if area and area in template_profile_ids and profile_manager:
            profile = await _get_profile(template_profile_ids[area])
            if profile is not None and field in profile.config:
                config_data[field] = profile.config[field]

        if field in template_overrides_dict:
            config_data[field] = template_overrides_dict[field]

    return config_data


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
