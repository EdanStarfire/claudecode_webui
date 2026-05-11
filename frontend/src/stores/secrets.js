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
  }
})
