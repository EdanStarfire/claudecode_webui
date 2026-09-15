import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../utils/api'

/**
 * Hook Config Store - Manages global hook configurations (Issue #1629)
 *
 * Handles CRUD for global, reusable hook configs that get attached to
 * sessions/templates/profiles by ID via SessionConfig.hook_ids. Mirrors
 * stores/mcpConfig.js's core CRUD (no OAuth/tools-check state — hooks have
 * no analog to either).
 */

// The ~11 documented Claude Code hook events, surfaced as autosuggest
// candidates in the entry editor. Any string is still accepted.
export const KNOWN_HOOK_EVENTS = [
  { name: 'PreToolUse', desc: 'Before a tool call executes (matcher = tool name)' },
  { name: 'PostToolUse', desc: 'After a tool call completes (matcher = tool name)' },
  { name: 'PostToolUseFailure', desc: 'After a tool call fails (matcher = tool name)' },
  { name: 'UserPromptSubmit', desc: 'When the user submits a prompt' },
  { name: 'Stop', desc: 'Right before Claude concludes its response' },
  { name: 'SubagentStart', desc: 'When a subagent (Agent tool) starts' },
  { name: 'SubagentStop', desc: 'Right before a subagent concludes' },
  { name: 'PreCompact', desc: 'Before conversation compaction (matcher = trigger)' },
  { name: 'Notification', desc: 'When notifications are sent' },
  { name: 'SessionStart', desc: 'When a new session starts (matcher = source)' },
  { name: 'SessionEnd', desc: 'When a session ends' },
  { name: 'PermissionRequest', desc: 'When a permission dialog is shown (matcher = tool name)' },
]

export const useHookConfigStore = defineStore('hookConfig', () => {
  // ========== STATE ==========

  const configs = ref(new Map())
  const loading = ref(false)

  // ========== GETTERS ==========

  function configList() {
    return Array.from(configs.value.values())
  }

  function getConfig(id) {
    return configs.value.get(id)
  }

  // ========== ACTIONS ==========

  async function fetchConfigs() {
    loading.value = true
    try {
      const data = await api.get('/api/hook-configs')
      configs.value = new Map()
      for (const config of (data.configs || [])) {
        configs.value.set(config.id, config)
      }
      configs.value = new Map(configs.value)
    } catch (error) {
      console.error('Failed to fetch hook configs:', error)
    } finally {
      loading.value = false
    }
  }

  async function createConfig(configData) {
    const config = await api.post('/api/hook-configs', configData)
    configs.value.set(config.id, config)
    configs.value = new Map(configs.value)
    return config
  }

  async function updateConfig(configId, configData) {
    const config = await api.put(`/api/hook-configs/${configId}`, configData)
    configs.value.set(config.id, config)
    configs.value = new Map(configs.value)
    return config
  }

  async function deleteConfig(configId) {
    await api.delete(`/api/hook-configs/${configId}`)
    configs.value.delete(configId)
    configs.value = new Map(configs.value)
  }

  return {
    configs,
    loading,
    configList,
    getConfig,
    fetchConfigs,
    createConfig,
    updateConfig,
    deleteConfig,
  }
})
