"""
Config Resolution

Resolves effective SessionConfig by merging profile defaults, template overrides,
and session-level overrides. Called at session start/restart so config changes
apply on the next restart (not live to running sessions).

3-tier resolution (high to low priority):
  session_overrides > template_overrides > profile values > template flat fields > defaults
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
        "docker_proxy_credential_names", "docker_proxy_allowlist_domains",
        "bare_mode", "env_scrub_enabled",
    },
    "features": {
        "history_distillation_enabled", "auto_memory_mode", "auto_memory_directory",
        "skill_creating_enabled",
    },
}

# The 6 valid profile area keys, for validation without exposing field details.
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
    "docker_proxy_credential_names", "docker_proxy_allowlist_domains",
})

# Fields that use additive (union) merge across tiers instead of last-wins.
# All tiers contribute their values; duplicates are removed and result is sorted.
ADDITIVE_LIST_FIELDS: frozenset[str] = frozenset({
    "docker_proxy_allowlist_domains",
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

    # Get base config from template + profiles (reuse resolve_template_config)
    config_data = await resolve_template_config(template, profile_manager)

    # Apply session_overrides (highest priority, last-wins for most fields)
    session_overrides = session_info.session_overrides or {}
    for field_name in CONFIG_FIELDS:
        if field_name in session_overrides:
            config_data[field_name] = session_overrides[field_name]

    # Additive merge for ADDITIVE_LIST_FIELDS: union all tiers instead of last-wins.
    # Must happen after standard resolution so all sources are consulted.
    if ADDITIVE_LIST_FIELDS:
        profile_cache: dict[str, object] = {}
        template_profile_ids = template.profile_ids or {}
        template_overrides_dict = template.template_overrides or {}
        for field in ADDITIVE_LIST_FIELDS:
            merged: set[str] = set()
            # Tier 1: template flat field
            flat_val = getattr(template, field, None)
            if flat_val:
                merged.update(flat_val if isinstance(flat_val, list) else [flat_val])
            # Tier 2: profile value for the field's area
            area = FIELD_TO_AREA.get(field)
            if area and area in template_profile_ids and profile_manager:
                profile = await _load_profile_cached(
                    template_profile_ids[area], profile_manager, profile_cache
                )
                if profile is not None and field in profile.config:
                    raw = profile.config[field]
                    coerced = _coerce_list(raw)
                    if coerced:
                        merged.update(coerced if isinstance(coerced, list) else [coerced])
            # Tier 3: template overrides
            if field in template_overrides_dict:
                val = template_overrides_dict[field]
                if val:
                    merged.update(val if isinstance(val, list) else [val])
            # Tier 4: session overrides
            if field in session_overrides:
                val = session_overrides[field]
                if val:
                    merged.update(val if isinstance(val, list) else [val])
            config_data[field] = sorted(merged) if merged else None

    # Carry template_id through for downstream reference
    config_data["template_id"] = session_info.template_id

    return SessionConfig(**config_data)


async def resolve_template_config(
    template,
    profile_manager: ProfileManager | None = None,
) -> dict:
    """Resolve template + profile values without session overrides.

    Returns a dict of config field -> value for use in minion spawn path.
    Shared helper between session-start and minion-spawn code paths.

    Priority (high to low): template_overrides > profile values > template flat fields
    """
    profile_cache: dict[str, object] = {}
    config_data: dict = {}
    template_profile_ids = template.profile_ids or {}
    template_overrides_dict = template.template_overrides or {}

    for field_name in CONFIG_FIELDS:
        if hasattr(template, field_name):
            config_data[field_name] = getattr(template, field_name)

        area = FIELD_TO_AREA.get(field_name)
        if area and area in template_profile_ids and profile_manager:
            profile = await _load_profile_cached(
                template_profile_ids[area], profile_manager, profile_cache
            )
            if profile is not None and field_name in profile.config:
                raw = profile.config[field_name]
                config_data[field_name] = _coerce_list(raw) if field_name in _LIST_FIELDS else raw

        if field_name in template_overrides_dict:
            config_data[field_name] = template_overrides_dict[field_name]

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
        docker_proxy_credential_names=getattr(session_info, "docker_proxy_credential_names", None),
        docker_proxy_allowlist_domains=getattr(session_info, "docker_proxy_allowlist_domains", None),
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
