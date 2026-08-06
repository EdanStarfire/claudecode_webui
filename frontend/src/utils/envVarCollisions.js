/**
 * Names of environment variables the app sets internally — mirrors
 * `_BACKGROUND_CALL_ENV_MAP` and `_resolve_env_vars()` in src/claude_sdk.py,
 * plus Docker/proxy vars set in src/session_coordinator.py. Used to warn (not
 * block) when a user-supplied `extra_env` key collides with one of these.
 */
export const MANAGED_ENV_VAR_NAMES = Object.freeze(new Set([
  'CLAUDE_CODE_ENABLE_TASKS',
  'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS',
  'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS',
  'CLAUDE_CODE_DISABLE_AUTO_MEMORY',
  'ENABLE_CLAUDEAI_MCP_SERVERS',
  'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB',
  'CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH',
  'CLAUDE_CODE_PROCESS_WRAPPER',
  'CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION',
  'CLAUDE_CODE_FORWARD_SUBAGENT_TEXT',
  'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
  'CLAUDE_CODE_DISABLE_CRON',
  'CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY',
  'CLAUDE_CODE_ENABLE_TELEMETRY',
  'CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK',
  'CLAUDE_CODE_DONT_INHERIT_ENV',
  'CLAUDE_CODE_ATTRIBUTION_HEADER',
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_API_KEY',
  'CLAUDE_DOCKER_EXTRA_ENV',
]))

/** Case-sensitive exact match against the managed env var name set. */
export function isManagedEnvVar(key) {
  return MANAGED_ENV_VAR_NAMES.has(key)
}
