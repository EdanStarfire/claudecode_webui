import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../utils/api'

/**
 * Secrets Store — host-level secrets vault CRUD + backend status.
 *
 * Issue #827: Host-level secrets storage via keyring.
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

  // ========== ACTIONS ==========

  async function fetchSecrets() {
    try {
      const result = await api.get('/api/secrets')
      secrets.value = result.secrets || []
    } catch (e) {
      console.error('Failed to fetch secrets:', e)
    }
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

  return {
    secrets,
    activeBackend,
    backendWarning,
    loading,
    error,
    fetchSecrets,
    createSecret,
    updateSecret,
    deleteSecret,
    fetchBackendStatus,
    refreshSecret,
  }
})
