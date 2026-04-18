/**
 * Profile area key constants.
 *
 * Single source of truth for the 6 configuration profile areas used
 * across stores, composables, and components. Mirrors the backend's
 * PROFILE_AREAS keys defined in src/config_resolution.py.
 */

export const PROFILE_AREAS = Object.freeze([
  'model',
  'permissions',
  'system_prompt',
  'mcp',
  'isolation',
  'features',
])

export const PROFILE_AREA_LABELS = Object.freeze({
  model: 'Model',
  permissions: 'Permissions',
  system_prompt: 'System Prompt',
  mcp: 'MCP',
  isolation: 'Isolation',
  features: 'Features',
})
