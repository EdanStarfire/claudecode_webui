import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../utils/api'

/**
 * MCP Config Store - Manages global MCP server configurations (Issue #676)
 *
 * Handles:
 * - CRUD operations for global MCP server definitions
 * - Loading configs for picker components
 */
export const OAUTH_STATUS = {
  AUTHENTICATED: 'authenticated',
  EXPIRING_SOON: 'expiring_soon',
  EXPIRED: 'expired',
  UNAUTHENTICATED: 'unauthenticated',
}

export const useMcpConfigStore = defineStore('mcpConfig', () => {
  // ========== STATE ==========

  // All configs by ID
  const configs = ref(new Map())

  // Loading state
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
      const data = await api.get('/api/mcp-configs')
      configs.value = new Map()
      for (const config of (data.configs || [])) {
        configs.value.set(config.id, config)
      }
      configs.value = new Map(configs.value)
    } catch (error) {
      console.error('Failed to fetch MCP configs:', error)
    } finally {
      loading.value = false
    }
  }

  async function createConfig(configData) {
    try {
      const config = await api.post('/api/mcp-configs', configData)
      configs.value.set(config.id, config)
      configs.value = new Map(configs.value)
      return config
    } catch (error) {
      console.error('Failed to create MCP config:', error)
      throw error
    }
  }

  async function updateConfig(configId, configData) {
    try {
      const config = await api.put(`/api/mcp-configs/${configId}`, configData)
      configs.value.set(config.id, config)
      configs.value = new Map(configs.value)
      return config
    } catch (error) {
      console.error('Failed to update MCP config:', error)
      throw error
    }
  }

  async function deleteConfig(configId) {
    try {
      await api.delete(`/api/mcp-configs/${configId}`)
      configs.value.delete(configId)
      configs.value = new Map(configs.value)
    } catch (error) {
      console.error('Failed to delete MCP config:', error)
      throw error
    }
  }

  async function exportConfigs(ids = null) {
    const body = ids ? { ids } : {}
    return await api.post('/api/mcp-configs/export', body)
  }

  async function importConfigs(servers, dry_run = true) {
    return await api.post('/api/mcp-configs/import', { servers, dry_run })
  }

  // ========== OAuth Actions ==========

  // Per-server OAuth status cache: configId → OAUTH_STATUS value
  const oauthStatus = ref(new Map())

  // Issue #1387: pending reconnect map — configId → baseName
  // Set before opening OAuth popup; cleared and acted on in mcp_oauth_complete handler.
  const pendingReconnect = ref(new Map())

  async function fetchOAuthStatus(configId) {
    try {
      const data = await api.get(`/api/mcp-configs/${configId}/oauth/status`)
      oauthStatus.value = new Map(oauthStatus.value.set(configId, data.status))
    } catch {
      oauthStatus.value = new Map(oauthStatus.value.set(configId, OAUTH_STATUS.UNAUTHENTICATED))
    }
  }

  async function initiateOAuth(configId) {
    // redirect_uri uses current origin — Vite proxies /oauth to the backend in dev,
    // and in production the backend serves everything directly
    const redirectUri = `${window.location.origin}/oauth/callback`
    return await api.post(`/api/mcp-configs/${configId}/oauth/initiate`, { redirect_uri: redirectUri })
  }

  async function disconnectOAuth(configId) {
    await api.post(`/api/mcp-configs/${configId}/oauth/disconnect`, {})
    oauthStatus.value = new Map(oauthStatus.value.set(configId, OAUTH_STATUS.UNAUTHENTICATED))
  }

  async function importOAuthAsSecret(configId, baseName) {
    return await api.post(`/api/mcp-configs/${configId}/oauth/import-as-secret`, { base_name: baseName })
  }

  // Issue #1387: Reconnect flow helpers

  /** Register a pending reconnect before opening the OAuth popup. */
  function startReconnect(configId, baseName) {
    pendingReconnect.value = new Map(pendingReconnect.value.set(configId, baseName))
  }

  /**
   * Complete a pending reconnect by calling import-as-secret with replace=true.
   * Called by the mcp_oauth_complete polling handler when a reconnect is pending.
   * Returns the API result or null if no pending reconnect for this configId.
   */
  async function completeReconnect(configId) {
    const baseName = pendingReconnect.value.get(configId)
    if (!baseName) return null
    const next = new Map(pendingReconnect.value)
    next.delete(configId)
    pendingReconnect.value = next
    return await api.post(
      `/api/mcp-configs/${configId}/oauth/import-as-secret`,
      { base_name: baseName, replace: true }
    )
  }

  return {
    configs,
    loading,
    oauthStatus,
    pendingReconnect,
    configList,
    getConfig,
    fetchConfigs,
    createConfig,
    updateConfig,
    deleteConfig,
    exportConfigs,
    importConfigs,
    fetchOAuthStatus,
    initiateOAuth,
    disconnectOAuth,
    importOAuthAsSecret,
    startReconnect,
    completeReconnect,
  }
})
