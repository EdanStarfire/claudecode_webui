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

  /** Live inbound connection status (stage 3a); merged into the relay record. */
  async function refreshInboundStatus(relayId) {
    const st = await api.get(`/api/relays/${encodeURIComponent(relayId)}/inbound/status`)
    const rec = relays.value.find(r => r.relay_id === relayId)
    if (rec) _upsert({ ...rec, inbound_status: st.status, inbound_detail: st.detail, inbound_enabled: st.enabled })
    return st
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

  // ── Policy editor / picker / audit (UI stage 2b) ─────────────────────────

  /** The backend sends structured errors as {code, message} in `detail`. */
  function errorMessage(e) {
    const detail = e?.data?.detail
    if (detail && typeof detail === 'object') return detail.message || detail.code || 'Request failed'
    return e?.message || 'Request failed'
  }

  const policyModels = new Map()

  /** The adapter-owned policy model (values, target pattern/label, value_help), cached. */
  async function getPolicyModel(relayId) {
    if (!policyModels.has(relayId)) {
      policyModels.set(relayId, await api.get(`/api/relays/${encodeURIComponent(relayId)}/policy-model`))
    }
    return policyModels.get(relayId)
  }

  /** Picker search. Rows: {id, kind, name, label, user?, is_bot?, dm_id?}. Throws Error(message). */
  async function searchConversations(relayId, { q, kinds, limit, cursor, signal } = {}) {
    const params = { q: q || null, kinds: kinds?.length ? kinds.join(',') : null, limit: limit || null, cursor: cursor || null }
    try {
      return await api.get(`/api/relays/${encodeURIComponent(relayId)}/conversations`, { params, signal })
    } catch (e) {
      if (e?.name === 'AbortError') throw e
      throw new Error(errorMessage(e))
    }
  }

  // Labels are bare-ID policy targets resolved to "#name" etc.; bounded, keyed relay+id.
  const LABEL_CACHE_MAX = 1000
  const LABEL_CHUNK = 50
  const labelCache = new Map()

  function cachedLabel(relayId, id) {
    return labelCache.get(`${relayId}:${id}`) || null
  }

  function rememberLabel(relayId, id, label) {
    const key = `${relayId}:${id}`
    labelCache.delete(key)
    labelCache.set(key, label)
    while (labelCache.size > LABEL_CACHE_MAX) labelCache.delete(labelCache.keys().next().value)
  }

  /** {id: label} for the ids; unresolved ids map to themselves. Never throws. */
  async function resolveLabels(relayId, ids) {
    const wanted = [...new Set(ids)]
    const missing = wanted.filter(id => !labelCache.has(`${relayId}:${id}`))
    for (let i = 0; i < missing.length; i += LABEL_CHUNK) {
      const chunk = missing.slice(i, i + LABEL_CHUNK)
      try {
        const { labels } = await api.get(`/api/relays/${encodeURIComponent(relayId)}/conversations`,
          { params: { ids: chunk.join(',') } })
        // an unresolved ID (label === id) is not cached, so a later call retries the lookup
        for (const id of chunk) {
          if (labels?.[id] && labels[id] !== id) rememberLabel(relayId, id, labels[id])
        }
      } catch {
        // best effort: the ID stays visible and editable; not cached so a later call retries
      }
    }
    return Object.fromEntries(wanted.map(id => [id, cachedLabel(relayId, id) || id]))
  }

  const _policyUrl = (sessionId, relayId, target) =>
    `/api/sessions/${encodeURIComponent(sessionId)}/relays/${encodeURIComponent(relayId)}/policies/${encodeURIComponent(target)}`

  /** Live session-tier edits; both return the refreshed GET /relay payload. */
  async function setPolicy(sessionId, relayId, target, policy) {
    try {
      return await api.put(_policyUrl(sessionId, relayId, target), { policy })
    } catch (e) {
      throw new Error(errorMessage(e))
    }
  }

  async function removePolicy(sessionId, relayId, target) {
    try {
      return await api.delete(_policyUrl(sessionId, relayId, target))
    } catch (e) {
      throw new Error(errorMessage(e))
    }
  }

  /** Audit records, newest first; `before` is the exclusive ISO timestamp cursor. */
  async function loadAudit(relayId, { sessionId, channel, limit, before } = {}) {
    const params = { session_id: sessionId || null, channel: channel || null, limit: limit || null, before: before || null }
    const { records } = await api.get(`/api/relays/${encodeURIComponent(relayId)}/audit`, { params })
    return records || []
  }

  return {
    getPolicyModel, searchConversations, resolveLabels, cachedLabel, setPolicy, removePolicy,
    loadAudit, errorMessage, rememberLabel,
    relays, relay, loaded, loading, error,
    loadRelays, fetchIfEmpty, createRelay, updateRelay, deleteRelay, saveSecrets, testRelay,
    refreshInboundStatus,
    loadManifest, oauthInitiate, oauthStatus, setSessionHandle, getSessionRelay,
  }
})
