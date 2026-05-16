"""
Config Resolution

Resolves effective SessionConfig by merging profile defaults, template config,
and session config. Called at session start/restart so config changes take
effect on the next restart (not live to running sessions).

3-tier resolution chain (high to low priority):
  session.config > template.config > profile.config > field defaults
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .session_config import CONFIG_FIELDS, SessionConfig
from .template_manager import TemplateManager

if TYPE_CHECKING:
    from .profile_manager import ProfileManager
    from .session_manager import SessionInfo

# 6 config areas — every field in CONFIG_FIELDS belongs to exactly one area.
PROFILE_AREAS: dict[str, set[str]] = {
    "model": {
        "model", "thinking_mode", "thinking_budget_tokens", "effort",
        "provider_catalog_id", "provider_model_id",
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
        "docker_proxy_allowlist_domains",
        "bare_mode", "env_scrub_enabled",
        "assigned_secrets", "extra_env",
    },
    "features": {
        "history_distillation_enabled", "auto_memory_mode", "auto_memory_directory",
        "skill_creating_enabled",
    },
}

# The valid profile area keys, for validation without exposing field details.
PROFILE_AREA_KEYS = frozenset(PROFILE_AREAS.keys())

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

# Fields that SessionConfig expects as list[str] but profiles may store as
# comma-separated strings (because TagInputWidget emits comma-separated values).
_LIST_FIELDS: frozenset[str] = frozenset({
    "allowed_tools", "disallowed_tools", "additional_directories",
    "setting_sources", "mcp_server_ids", "docker_extra_mounts",
    "assigned_secrets", "docker_proxy_allowlist_domains",
})


def _coerce_list(value: object) -> object:
    """Convert a comma-separated string to a list for fields that require list[str]."""
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",") if s.strip()]
        return items if items else None
    return value


async def _load_profile_cached(
    profile_id: str,
    profile_manager: ProfileManager | None,
    cache: dict[str, object],
) -> object:
    """Fetch a profile, using *cache* to avoid repeated lookups within one resolution."""
    if profile_id not in cache:
        if profile_manager:
            cache[profile_id] = await profile_manager.get_profile(profile_id)
        else:
            cache[profile_id] = None
    return cache[profile_id]


async def resolve_effective_config(
    session_info: SessionInfo,
    template_manager: TemplateManager,
    profile_manager: ProfileManager | None = None,
) -> SessionConfig:
    """Build effective SessionConfig from the 3-tier resolution chain.

    Priority (high to low):
      session.config > template.config > profile.config > field defaults

    Called in start_session() — config takes effect at the next session start/restart.
    Template updates between restarts are invisible to running sessions.

    Args:
        session_info: Session state (template_id, config dict).
        template_manager: Used to fetch the linked template.
        profile_manager: Optional; enables profile resolution from template.profile_ids.
    """
    config_data: dict = {}

    # Layer 1: profile values + template.config (if template linked)
    if session_info.template_id:
        template = await template_manager.get_template(session_info.template_id)
        if template:
            profile_cache: dict = {}
            for _area, profile_id in (template.profile_ids or {}).items():
                profile = await _load_profile_cached(profile_id, profile_manager, profile_cache)
                if profile:
                    for k, v in (profile.config or {}).items():
                        if k in CONFIG_FIELDS:
                            config_data[k] = _coerce_list(v) if k in _LIST_FIELDS else v
            for k, v in (template.config or {}).items():
                if k in CONFIG_FIELDS:
                    config_data[k] = v

    # Layer 2: session.config (highest priority)
    for k, v in (session_info.config or {}).items():
        if k in CONFIG_FIELDS:
            config_data[k] = v

    # Filter orphaned keys (fields removed from CONFIG_FIELDS in a future release)
    known = {k: v for k, v in config_data.items() if k in CONFIG_FIELDS}
    known["template_id"] = session_info.template_id
    return SessionConfig(**known)


async def resolve_template_config(
    template,
    profile_manager: ProfileManager | None = None,
) -> dict:
    """Resolve template + profile values without session overrides.

    Returns a dict of config field -> value for use in the minion spawn path.
    Shared helper between session-start and minion-spawn code paths.

    Priority (high to low): template.config > profile.config > field defaults
    """
    profile_cache: dict = {}
    config_data: dict = {}
    template_profile_ids = template.profile_ids or {}

    for _area, profile_id in template_profile_ids.items():
        profile = await _load_profile_cached(profile_id, profile_manager, profile_cache)
        if profile:
            for k, v in (profile.config or {}).items():
                if k in CONFIG_FIELDS:
                    config_data[k] = _coerce_list(v) if k in _LIST_FIELDS else v

    for k, v in (template.config or {}).items():
        if k in CONFIG_FIELDS:
            config_data[k] = v

    return config_data
