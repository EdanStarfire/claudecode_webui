/**
 * CONFIG_FIELDS — mirrors backend src/session_config.py CONFIG_FIELDS and
 * src/config_resolution.py PROFILE_AREAS / FIELD_TO_AREA.
 *
 * Single source of truth for the frontend. Used by useFieldState composable
 * to compute per-field resolution metadata.
 */

export const CONFIG_FIELDS_LIST = Object.freeze([
  // permissions area
  'permission_mode',
  'allowed_tools',
  'disallowed_tools',
  'additional_directories',
  'setting_sources',
  // system_prompt area
  'system_prompt',
  'override_system_prompt',
  // model area
  'model',
  'thinking_mode',
  'thinking_budget_tokens',
  'effort',
  // mcp area
  'mcp_server_ids',
  'enable_claudeai_mcp_servers',
  'strict_mcp_config',
  // isolation area
  'cli_path',
  'sandbox_enabled',
  'sandbox_config',
  'docker_enabled',
  'docker_image',
  'docker_extra_mounts',
  'docker_home_directory',
  'docker_proxy_enabled',
  'docker_proxy_image',
  'docker_proxy_allowlist_domains',
  'assigned_secrets',
  'bare_mode',
  'env_scrub_enabled',
  // features area
  'history_distillation_enabled',
  'auto_memory_mode',
  'auto_memory_directory',
  'skill_creating_enabled',
])

/** Default value for each CONFIG_FIELD — mirrors SessionConfig defaults. */
export const FIELD_DEFAULTS = Object.freeze({
  permission_mode: 'acceptEdits',
  system_prompt: null,
  override_system_prompt: false,
  allowed_tools: null,
  disallowed_tools: null,
  model: null,
  thinking_mode: null,
  thinking_budget_tokens: null,
  effort: null,
  additional_directories: null,
  cli_path: null,
  setting_sources: null,
  sandbox_enabled: false,
  sandbox_config: null,
  docker_enabled: false,
  docker_image: null,
  docker_extra_mounts: null,
  docker_home_directory: null,
  docker_proxy_enabled: false,
  docker_proxy_image: null,
  assigned_secrets: null,
  docker_proxy_allowlist_domains: null,
  history_distillation_enabled: true,
  auto_memory_mode: 'claude',
  auto_memory_directory: null,
  skill_creating_enabled: false,
  mcp_server_ids: null,
  enable_claudeai_mcp_servers: true,
  strict_mcp_config: false,
  bare_mode: false,
  env_scrub_enabled: false,
})

/** Reverse lookup: field name → profile area key. */
const _FIELD_TO_AREA = Object.freeze({
  // model
  model: 'model',
  thinking_mode: 'model',
  thinking_budget_tokens: 'model',
  effort: 'model',
  // permissions
  permission_mode: 'permissions',
  allowed_tools: 'permissions',
  disallowed_tools: 'permissions',
  additional_directories: 'permissions',
  setting_sources: 'permissions',
  // system_prompt
  system_prompt: 'system_prompt',
  override_system_prompt: 'system_prompt',
  // mcp
  mcp_server_ids: 'mcp',
  enable_claudeai_mcp_servers: 'mcp',
  strict_mcp_config: 'mcp',
  // isolation
  cli_path: 'isolation',
  sandbox_enabled: 'isolation',
  sandbox_config: 'isolation',
  docker_enabled: 'isolation',
  docker_image: 'isolation',
  docker_extra_mounts: 'isolation',
  docker_home_directory: 'isolation',
  docker_proxy_enabled: 'isolation',
  docker_proxy_image: 'isolation',
  docker_proxy_allowlist_domains: 'isolation',
  assigned_secrets: 'isolation',
  bare_mode: 'isolation',
  env_scrub_enabled: 'isolation',
  // features
  history_distillation_enabled: 'features',
  auto_memory_mode: 'features',
  auto_memory_directory: 'features',
  skill_creating_enabled: 'features',
})

/** Return the profile area for a CONFIG_FIELD, or null if unknown. */
export function fieldArea(fieldName) {
  return _FIELD_TO_AREA[fieldName] ?? null
}
