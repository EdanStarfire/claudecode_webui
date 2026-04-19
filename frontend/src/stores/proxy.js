import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../utils/api'

/**
 * Proxy Store — credential vault + per-session proxy status/blocked log.
 *
 * Issue #1053: Domain allowlist and credential management UI for proxy mode.
 */
export const useProxyStore = defineStore('proxy', () => {
  // ========== STATE ==========

  /** List of credential metadata objects (no values) */
  const credentials = ref([])

  /** Per-session proxy status: sessionId -> { proxy_enabled, effective_domains, active_credentials, sidecar_running } */
  const statusBySession = ref(new Map())

  /** Per-session blocked connection log: sessionId -> [entries] */
  const blockedLogsBySession = ref(new Map())

  // ========== ACTIONS ==========

  async function fetchCredentials() {
    try {
      const result = await api.get('/api/proxy/credentials')
      credentials.value = result.credentials || []
    } catch (e) {
      console.error('Failed to fetch proxy credentials:', e)
    }
  }

  async function createCredential(data) {
    const result = await api.post('/api/proxy/credentials', data)
    // Refresh list after create
    await fetchCredentials()
    return result
  }

  async function deleteCredential(name) {
    await api.delete(`/api/proxy/credentials/${encodeURIComponent(name)}`)
    await fetchCredentials()
  }

  async function fetchProxyStatus(sessionId) {
    if (!sessionId) return
    try {
      const result = await api.get(`/api/sessions/${sessionId}/proxy/status`)
      statusBySession.value.set(sessionId, result)
    } catch (e) {
      console.error('Failed to fetch proxy status:', e)
    }
  }

  async function fetchBlockedLog(sessionId) {
    if (!sessionId) return
    try {
      const result = await api.get(`/api/sessions/${sessionId}/proxy/blocked`)
      blockedLogsBySession.value.set(sessionId, result.entries || [])
    } catch (e) {
      console.error('Failed to fetch proxy blocked log:', e)
    }
  }

  function proxyStatus(sessionId) {
    return statusBySession.value.get(sessionId) || null
  }

  function blockedLog(sessionId) {
    return blockedLogsBySession.value.get(sessionId) || []
  }

  return {
    credentials,
    statusBySession,
    blockedLogsBySession,
    fetchCredentials,
    createCredential,
    deleteCredential,
    fetchProxyStatus,
    fetchBlockedLog,
    proxyStatus,
    blockedLog,
  }
})
