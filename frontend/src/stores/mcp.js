import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { apiGet, apiPost } from '../utils/api'

/**
 * MCP Store - Manages MCP server status per session
 *
 * Issue #675: Shows MCP server status in Config Modal and Session Info Modal.
 * Supports toggling servers on/off and reconnecting failed servers.
 */
export const useMcpStore = defineStore('mcp', () => {
  // ========== STATE ==========

  // MCP status per session (sessionId -> { servers: [...] })
  const mcpBySession = ref(new Map())

  // Loading state per session
  const loadingBySession = ref(new Map())

  // ========== COMPUTED ==========

  /**
   * Get MCP servers for the current session
   */
  const currentServers = computed(() => {
    const sessionStore = useSessionStore()
    if (!sessionStore.currentSessionId) return []
    const data = mcpBySession.value.get(sessionStore.currentSessionId)
    return data?.mcpServers || []
  })

  // ========== ACTIONS ==========

  async function fetchMcpStatus(sessionId) {
    if (!sessionId) return
    loadingBySession.value.set(sessionId, true)
    try {
      const result = await apiGet(`/api/sessions/${sessionId}/mcp-status`)
      mcpBySession.value.set(sessionId, result)
    } catch (e) {
      console.error('Failed to fetch MCP status:', e)
      mcpBySession.value.set(sessionId, { mcpServers: [] })
    } finally {
      loadingBySession.value.set(sessionId, false)
    }
  }

  async function toggleServer(sessionId, name, enabled) {
    try {
      await apiPost(`/api/sessions/${sessionId}/mcp-toggle`, { name, enabled })
      await fetchMcpStatus(sessionId)
    } catch (e) {
      console.error('Failed to toggle MCP server:', e)
    }
  }

  async function reconnectServer(sessionId, name) {
    try {
      await apiPost(`/api/sessions/${sessionId}/mcp-reconnect`, { name })
      await fetchMcpStatus(sessionId)
    } catch (e) {
      console.error('Failed to reconnect MCP server:', e)
    }
  }

  function mcpServers(sessionId) {
    const data = mcpBySession.value.get(sessionId)
    return data?.mcpServers || []
  }

  function isLoading(sessionId) {
    return loadingBySession.value.get(sessionId) || false
  }

  return {
    mcpBySession,
    loadingBySession,
    currentServers,
    fetchMcpStatus,
    toggleServer,
    reconnectServer,
    mcpServers,
    isLoading,
  }
})
