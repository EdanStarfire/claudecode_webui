import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

/**
 * Relay Store — Slack relay Library asset (issue #2103, UI stage 2a).
 *
 * The Backend is the source of truth. Only public relay records (has_* booleans and the NAMES
 * of the referenced vault secrets, never token values) are held here: credentials are
 * references to existing secrets, the browser never handles a token value. Errors are thrown
 * as `Error` so the calling component can render `error.message`.
 */
export const useRelayStore = defineStore('relay', () => {
  const relays = ref([])
  const loaded = ref(false)
  const loading = ref(false)
  const error = ref(null)

  /** One relay per Backend today; the first record is the active one. */
  const relay = computed(() => relays.value[0] || null)

  function _upsert(record) {
    const i = relays.value.findIndex(r => r.relay_id === record.relay_id)
    if (i >= 0) relays.value.splice(i, 1, record)
    else relays.value.push(record)
  }

  async function loadRelays() {
    loading.value = true
    error.value = null
    try {
      const result = await api.get('/api/relays')
      relays.value = result.relays || []
      loaded.value = true
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function fetchIfEmpty() {
    if (loaded.value) return
    await loadRelays()
  }

  async function createRelay(name, adapterType = 'slack') {
    const record = await api.post('/api/relays', { name, adapter_type: adapterType })
    _upsert(record)
    return record
  }

  async function updateRelay(relayId, patch) {
    const record = await api.put(`/api/relays/${encodeURIComponent(relayId)}`, patch)
    _upsert(record)
    return record
  }

  async function deleteRelay(relayId) {
    await api.delete(`/api/relays/${encodeURIComponent(relayId)}`)
    relays.value = relays.value.filter(r => r.relay_id !== relayId)
  }

  /** Reference existing vault secrets by NAME; the Backend validates and verifies them. */
  async function saveSecrets(relayId, { userSecret, appSecret }) {
    const body = {}
    if (userSecret) body.user_token_secret = userSecret
    if (appSecret) body.app_token_secret = appSecret
    const record = await api.put(`/api/relays/${encodeURIComponent(relayId)}/secrets`, body)
    _upsert(record)
    return record
  }

  /** POST /test; also refreshes the record (workspace / owner are learned by the test). */
  async function testRelay(relayId) {
    const result = await api.post(`/api/relays/${encodeURIComponent(relayId)}/test`, {})
    try {
      _upsert(await api.get(`/api/relays/${encodeURIComponent(relayId)}`))
    } catch {
      // the test result itself is what matters
    }
    return result
  }

  async function loadManifest(relayId, includeInbound = true) {
    const q = includeInbound ? '' : '?include_inbound=false'
    return api.get(`/api/relays/${encodeURIComponent(relayId)}/manifest${q}`)
  }

  async function oauthInitiate(relayId, redirectUri) {
    return api.post(`/api/relays/${encodeURIComponent(relayId)}/oauth/initiate`, {
      redirect_uri: redirectUri,
    })
  }

  async function oauthStatus(relayId, state) {
    return api.get(
      `/api/relays/${encodeURIComponent(relayId)}/oauth/status?state=${encodeURIComponent(state)}`
    )
  }

  /** Session-tier handle: dedicated endpoint (the backend keeps the handle independent). */
  async function setSessionHandle(sessionId, handle) {
    return api.put(`/api/sessions/${encodeURIComponent(sessionId)}/relay/handle`, { handle })
  }

  async function getSessionRelay(sessionId) {
    return api.get(`/api/sessions/${encodeURIComponent(sessionId)}/relay`)
  }

  return {
    relays, relay, loaded, loading, error,
    loadRelays, fetchIfEmpty, createRelay, updateRelay, deleteRelay, saveSecrets, testRelay,
    loadManifest, oauthInitiate, oauthStatus, setSessionHandle, getSessionRelay,
  }
})
