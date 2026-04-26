import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/utils/api'

export const useEditHistoryStore = defineStore('editHistory', () => {
  // sessionId -> { entries, tool_count, loading, error }
  const entriesBySession = ref(new Map())
  // composite key: `${sessionId}:${tool_use_id}`
  const expandedKeys = ref(new Set())
  const showNonModifyingBash = ref(false)

  async function loadHistory(sessionId) {
    if (!sessionId) return
    const cur = entriesBySession.value.get(sessionId) || {}
    entriesBySession.value.set(sessionId, { ...cur, loading: true, error: null })
    try {
      const data = await api.get(`/api/sessions/${sessionId}/edit-history`)
      entriesBySession.value.set(sessionId, {
        entries: data.entries || [],
        tool_count: data.tool_count || 0,
        loading: false,
        error: null,
      })
    } catch (e) {
      entriesBySession.value.set(sessionId, {
        entries: [],
        tool_count: 0,
        loading: false,
        error: e.message || 'Failed to load edit history',
      })
    }
  }

  async function refreshHistory(sessionId) {
    entriesBySession.value.delete(sessionId)
    await loadHistory(sessionId)
  }

  function toggleExpand(sessionId, toolUseId) {
    const key = `${sessionId}:${toolUseId}`
    if (expandedKeys.value.has(key)) {
      expandedKeys.value.delete(key)
    } else {
      expandedKeys.value.add(key)
    }
    // Trigger reactivity
    expandedKeys.value = new Set(expandedKeys.value)
  }

  function isExpanded(sessionId, toolUseId) {
    return expandedKeys.value.has(`${sessionId}:${toolUseId}`)
  }

  function visibleEntries(sessionId) {
    const cur = entriesBySession.value.get(sessionId)
    if (!cur) return []
    if (showNonModifyingBash.value) return cur.entries || []
    return (cur.entries || []).filter(e => e.tool_name !== 'Bash' || e.likely_modifying !== false)
  }

  function entryCount(sessionId) {
    return visibleEntries(sessionId).length
  }

  function clearHistory(sessionId) {
    entriesBySession.value.delete(sessionId)
    // Clear expanded keys for this session
    const prefix = `${sessionId}:`
    const next = new Set([...expandedKeys.value].filter(k => !k.startsWith(prefix)))
    expandedKeys.value = next
  }

  return {
    entriesBySession,
    showNonModifyingBash,
    loadHistory,
    refreshHistory,
    toggleExpand,
    isExpanded,
    visibleEntries,
    entryCount,
    clearHistory,
  }
})
