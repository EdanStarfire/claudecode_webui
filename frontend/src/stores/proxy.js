import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, apiGet } from '../utils/api'
import { useSessionStore } from './session'

/**
 * Proxy Store — credential vault + per-session proxy status/blocked log + proxy access logs.
 *
 * Issue #1053: Domain allowlist and credential management UI for proxy mode.
 * Issue #1102: Proxy log tab — live feed of HTTP access log and DNS queries.
 */
export const useProxyStore = defineStore('proxy', () => {
  // ========== STATE ==========

  /** List of credential metadata objects (no values) */
  const credentials = ref([])

  /** Per-session proxy status: sessionId -> { proxy_enabled, effective_domains, active_credentials, sidecar_running } */
  const statusBySession = ref(new Map())

  /** Per-session blocked connection log: sessionId -> [entries] */
  const blockedLogsBySession = ref(new Map())

  // Issue #1102: Log data per session
  // sessionId -> { http: { entries, total_lines }, dns: { entries, total_lines } }
  const logsBySession = ref(new Map())

  const loading = ref(false)
  const error = ref(null)

  // Active sub-tab: 'http' or 'dns'
  const activeSubTab = ref('http')

  // Polling interval ID
  const pollingInterval = ref(null)

  // ========== COMPUTED (Issue #1102) ==========

  const currentHttpLogs = computed(() => {
    const sessionStore = useSessionStore()
    if (!sessionStore.currentSessionId) return []
    return logsBySession.value.get(sessionStore.currentSessionId)?.http?.entries ?? []
  })

  const currentDnsLogs = computed(() => {
    const sessionStore = useSessionStore()
    if (!sessionStore.currentSessionId) return []
    return logsBySession.value.get(sessionStore.currentSessionId)?.dns?.entries ?? []
  })

  const currentHttpCount = computed(() => {
    const sessionStore = useSessionStore()
    if (!sessionStore.currentSessionId) return 0
    return logsBySession.value.get(sessionStore.currentSessionId)?.http?.total_lines ?? 0
  })

  const currentDnsCount = computed(() => {
    const sessionStore = useSessionStore()
    if (!sessionStore.currentSessionId) return 0
    return logsBySession.value.get(sessionStore.currentSessionId)?.dns?.total_lines ?? 0
  })

  const currentTotalCount = computed(() => currentHttpCount.value + currentDnsCount.value)

  // ========== ACTIONS (Issue #1053) ==========

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

  // ========== ACTIONS (Issue #1102) ==========

  /**
   * Load log entries for one log type (http or dns)
   */
  async function loadLogs(sessionId, logType) {
    if (!sessionId) return
    try {
      const response = await apiGet(`/api/sessions/${sessionId}/proxy-logs`, {
        params: { log_type: logType, limit: 200 }
      })
      const current = logsBySession.value.get(sessionId) ?? {}
      logsBySession.value.set(sessionId, {
        ...current,
        [logType]: { entries: response.entries, total_lines: response.total_lines }
      })
      logsBySession.value = new Map(logsBySession.value)
      error.value = null
    } catch (err) {
      console.error(`Failed to load ${logType} proxy logs for session ${sessionId}:`, err)
      error.value = err.message
    }
  }

  /**
   * Load both HTTP and DNS logs for a session
   */
  async function loadAllLogs(sessionId) {
    if (!sessionId) return
    loading.value = true
    try {
      await Promise.all([loadLogs(sessionId, 'http'), loadLogs(sessionId, 'dns')])
    } finally {
      loading.value = false
    }
  }

  /**
   * Start 3-second polling for proxy logs
   */
  function startPolling(sessionId) {
    stopPolling()
    if (!sessionId) return
    pollingInterval.value = setInterval(() => {
      loadAllLogs(sessionId)
    }, 3000)
  }

  /**
   * Stop polling
   */
  function stopPolling() {
    if (pollingInterval.value !== null) {
      clearInterval(pollingInterval.value)
      pollingInterval.value = null
    }
  }

  /**
   * Set active sub-tab
   */
  function setSubTab(tab) {
    activeSubTab.value = tab
  }

  /**
   * Clear log data for a session
   */
  function clearSession(sessionId) {
    logsBySession.value.delete(sessionId)
    logsBySession.value = new Map(logsBySession.value)
  }

  return {
    // Issue #1053
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

    // Issue #1102
    logsBySession,
    loading,
    error,
    activeSubTab,
    pollingInterval,
    currentHttpLogs,
    currentDnsLogs,
    currentHttpCount,
    currentDnsCount,
    currentTotalCount,
    loadLogs,
    loadAllLogs,
    startPolling,
    stopPolling,
    setSubTab,
    clearSession,
  }
})
