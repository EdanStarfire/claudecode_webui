import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

export const useProviderCatalogStore = defineStore('providerCatalog', () => {
  const entries  = ref([])
  const status   = ref({
    running: false, port: null, pending_changes: false,
    model_count: 0, last_restart: null, last_error: null,
  })
  const loading    = ref(false)
  const loaded     = ref(false)
  const restarting = ref(false)

  const pendingChanges = computed(() => status.value.pending_changes || false)

  function getEntry(id) {
    return entries.value.find(e => e.id === id)
  }

  function modelsForEntry(id) {
    return getEntry(id)?.models || []
  }

  async function fetchEntries() {
    loading.value = true
    try {
      const result = await api.get('/api/provider-catalog')
      entries.value = result.entries || []
      status.value = { ...status.value, pending_changes: result.pending_changes || false }
      loaded.value = true
    } catch (e) {
      console.error('Failed to fetch provider catalog:', e)
    } finally {
      loading.value = false
    }
  }

  async function fetchIfEmpty() {
    if (loaded.value) return
    await fetchEntries()
  }

  async function fetchStatus() {
    try {
      const result = await api.get('/api/provider-catalog/status')
      status.value = result
    } catch (e) {
      console.error('Failed to fetch provider catalog status:', e)
    }
  }

  async function createEntry(data) {
    const result = await api.post('/api/provider-catalog', data)
    await fetchEntries()
    return result
  }

  async function updateEntry(id, data) {
    const result = await api.put(`/api/provider-catalog/${encodeURIComponent(id)}`, data)
    await fetchEntries()
    return result
  }

  async function deleteEntry(id) {
    await api.delete(`/api/provider-catalog/${encodeURIComponent(id)}`)
    await fetchEntries()
  }

  async function restartProxy() {
    restarting.value = true
    try {
      const result = await api.post('/api/provider-catalog/restart', {})
      status.value = result
    } finally {
      restarting.value = false
    }
  }

  return {
    entries, pendingChanges, status, loading, loaded, restarting,
    getEntry, modelsForEntry,
    fetchEntries, fetchIfEmpty, fetchStatus,
    createEntry, updateEntry, deleteEntry, restartProxy,
  }
})
