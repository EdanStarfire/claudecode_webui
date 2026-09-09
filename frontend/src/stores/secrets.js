import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../utils/api'

/**
 * Secrets Store — host-level secrets vault CRUD + backend status.
 *
 * Issue #827: Host-level secrets storage via keyring.
 * Issue #1387: Token health tracking + event handlers for vault OAuth2 refresh.
 */
export const useSecretsStore = defineStore('secrets', () => {
  // ========== STATE ==========

  /** List of secret metadata objects (no values) */
  const secrets = ref([])

  /** Active keyring backend name (e.g. "SecretService", "CryptFileKeyring") */
  const activeBackend = ref('')

  /** Warning message when backend is degraded (e.g. CryptFileKeyring without password) */
  const backendWarning = ref(null)

  const loading = ref(false)
  const error = ref(null)
  const loaded = ref(false)

  // ========== ACTIONS ==========

  async function fetchSecrets() {
    try {
      const result = await api.get('/api/secrets')
      secrets.value = result.secrets || []
      loaded.value = true
    } catch (e) {
      console.error('Failed to fetch secrets:', e)
    }
  }

  async function fetchIfEmpty() {
    if (loaded.value) return
    await fetchSecrets()
  }

  async function createSecret(data) {
    const result = await api.post('/api/secrets', data)
    await fetchSecrets()
    return result
  }

  async function updateSecret(name, data) {
    const result = await api.patch(`/api/secrets/${encodeURIComponent(name)}`, data)
    await fetchSecrets()
    return result
  }

  async function deleteSecret(name) {
    await api.delete(`/api/secrets/${encodeURIComponent(name)}`)
    await fetchSecrets()
  }

  async function fetchBackendStatus() {
    try {
      const result = await api.get('/api/system/secrets-backend-status')
      activeBackend.value = result.backend || ''
      backendWarning.value = result.warning || null
    } catch (e) {
      console.error('Failed to fetch secrets backend status:', e)
    }
  }

  async function refreshSecret(name) {
    const result = await api.post(`/api/secrets/${encodeURIComponent(name)}/refresh`, {})
    await fetchSecrets()
    return result
  }

  // ========== Issue #1387: Health helpers + event handlers ==========

  /**
   * Derive token health state for a vault oauth2 secret by name.
   * Returns "valid" | "expiring_soon" | "expired" | "refresh_failed" | null (non-oauth2).
   */
  function healthFor(name) {
    const secret = secrets.value.find(s => s.name === name)
    if (!secret || secret.type !== 'oauth2') return null
    return secret.health || 'valid'
  }

  /** Handle secret_refreshed UI poll event — optimistic update + background refetch. */
  function handleSecretRefreshed(secretName) {
    const idx = secrets.value.findIndex(s => s.name === secretName)
    if (idx >= 0) {
      const updated = { ...secrets.value[idx], health: 'valid' }
      const refresh = updated.refresh ? { ...updated.refresh, last_refresh_error: null } : updated.refresh
      updated.refresh = refresh
      secrets.value = [
        ...secrets.value.slice(0, idx),
        updated,
        ...secrets.value.slice(idx + 1),
      ]
    }
    fetchSecrets()
  }

  /** Handle secret_refresh_failed UI poll event — mark health as failed. */
  function handleSecretRefreshFailed(secretName, errorMsg) {
    const idx = secrets.value.findIndex(s => s.name === secretName)
    if (idx >= 0) {
      const updated = { ...secrets.value[idx], health: 'refresh_failed' }
      if (updated.refresh) {
        updated.refresh = { ...updated.refresh, last_refresh_error: errorMsg }
      }
      secrets.value = [
        ...secrets.value.slice(0, idx),
        updated,
        ...secrets.value.slice(idx + 1),
      ]
    }
    fetchSecrets()
  }

  // ========== Issue #1871: Standalone OAuth2 guided-authorization flow ==========

  /** flow_id -> true while a guided-authorization popup is open and awaiting completion. */
  const pendingOAuthFlows = ref(new Map())

  /** flow_id -> {success, secretName, error, errorCode} once secret_oauth_complete
   * arrives. Consumed (and removed) by the panel component via consumeOAuthFlowResult(). */
  const oauthFlowResults = ref(new Map())

  /**
   * Build the redirect_uri for a standalone OAuth flow, honoring a custom callback
   * path/port (issue #1789's callback customization, extended to standalone secrets).
   * Mirrors mcpConfig.js's buildRedirectUri() — built from protocol + hostname (not
   * window.location.origin) so a custom port override doesn't pick up the browser's
   * *current* port when the operator's OAuth app is registered against a different one.
   */
  function buildOAuthRedirectUri(customCallbackPath, customCallbackPort) {
    const protocol = window.location.protocol
    const hostname = window.location.hostname
    const path = customCallbackPath || '/oauth/callback'
    const authority = customCallbackPort ? `${hostname}:${customCallbackPort}` : window.location.host
    return `${protocol}//${authority}${path}`
  }

  async function initiateOAuth(payload) {
    const result = await api.post('/api/secrets/oauth/initiate', payload)
    pendingOAuthFlows.value = new Map(pendingOAuthFlows.value.set(result.flow_id, true))
    return result
  }

  async function initiateReconnect(secretName, payload) {
    const result = await api.post(
      `/api/secrets/${encodeURIComponent(secretName)}/oauth/reconnect-initiate`,
      payload
    )
    pendingOAuthFlows.value = new Map(pendingOAuthFlows.value.set(result.flow_id, true))
    return result
  }

  async function cancelOAuthFlow(flowId) {
    if (!flowId) return
    const next = new Map(pendingOAuthFlows.value)
    next.delete(flowId)
    pendingOAuthFlows.value = next
    try {
      await api.post(`/api/secrets/oauth/${encodeURIComponent(flowId)}/cancel`, {})
    } catch (e) {
      console.error('Failed to cancel standalone OAuth flow:', e)
    }
  }

  /** Handle secret_oauth_complete UI poll event — fires on both success and failure
   * (unlike mcp_oauth_complete) since the panel has no other way to learn the outcome
   * of a flow completed in a cross-origin popup. */
  function handleSecretOAuthComplete(payload) {
    const flowId = payload?.flow_id
    if (!flowId) return
    const next = new Map(pendingOAuthFlows.value)
    next.delete(flowId)
    pendingOAuthFlows.value = next
    oauthFlowResults.value = new Map(oauthFlowResults.value.set(flowId, {
      success: !!payload.success,
      secretName: payload.secret_name || null,
      error: payload.error || null,
      errorCode: payload.error_code || null,
    }))
    if (payload.success) fetchSecrets()
  }

  /** Read and remove a completed flow's result — called once by the panel watching
   * for its own flow_id, so a stale result can't be "seen" twice. */
  function consumeOAuthFlowResult(flowId) {
    const result = oauthFlowResults.value.get(flowId)
    if (result) {
      const next = new Map(oauthFlowResults.value)
      next.delete(flowId)
      oauthFlowResults.value = next
    }
    return result || null
  }

  return {
    secrets,
    activeBackend,
    backendWarning,
    loading,
    error,
    loaded,
    fetchSecrets,
    fetchIfEmpty,
    createSecret,
    updateSecret,
    deleteSecret,
    fetchBackendStatus,
    refreshSecret,
    healthFor,
    handleSecretRefreshed,
    handleSecretRefreshFailed,
    pendingOAuthFlows,
    oauthFlowResults,
    buildOAuthRedirectUri,
    initiateOAuth,
    initiateReconnect,
    cancelOAuthFlow,
    handleSecretOAuthComplete,
    consumeOAuthFlowResult,
  }
})
