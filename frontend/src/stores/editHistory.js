import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { api } from '@/utils/api'
import { useMessageStore } from './message'

export const useEditHistoryStore = defineStore('editHistory', () => {
  // sessionId -> { entries, tool_count, loading, error }
  const entriesBySession = ref(new Map())
  // composite key: `${sessionId}:${tool_use_id}`
  const expandedKeys = ref(new Set())
  const showNonModifyingBash = ref(false)

  // Dedup: sessionId -> Set<tool_use_id> of known terminal tool calls
  const seenCompletedTools = new Map()
  // Debounce: sessionId -> timeoutId
  const refreshTimers = new Map()

  function scheduleDebouncedRefresh(sessionId) {
    if (refreshTimers.has(sessionId)) {
      clearTimeout(refreshTimers.get(sessionId))
    }
    const timerId = setTimeout(() => {
      refreshTimers.delete(sessionId)
      refreshHistory(sessionId)
    }, 250)
    refreshTimers.set(sessionId, timerId)
  }

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
    // Clear dedup state and any pending debounce timer
    seenCompletedTools.delete(sessionId)
    if (refreshTimers.has(sessionId)) {
      clearTimeout(refreshTimers.get(sessionId))
      refreshTimers.delete(sessionId)
    }
  }

  // Reactive cross-watch: auto-refresh when Edit/Write/Bash tool calls complete.
  // useMessageStore() is called inside setup (not at module load) per Pinia best practice.
  {
    const messageStore = useMessageStore()
    watch(
      () => {
        const result = []
        for (const [sessionId, calls] of messageStore.toolCallsBySession) {
          for (const c of calls) {
            if (
              ['Edit', 'Write', 'Bash'].includes(c.name) &&
              (c.status === 'completed' || c.status === 'error')
            ) {
              result.push({ sessionId, toolUseId: c.id })
            }
          }
        }
        return result
      },
      (snapshot) => {
        const newlyCompleted = new Map()
        for (const { sessionId, toolUseId } of snapshot) {
          let seen = seenCompletedTools.get(sessionId)
          if (!seen) {
            seen = new Set()
            seenCompletedTools.set(sessionId, seen)
          }
          if (!seen.has(toolUseId)) {
            seen.add(toolUseId)
            newlyCompleted.set(sessionId, (newlyCompleted.get(sessionId) || 0) + 1)
          }
        }
        for (const sessionId of newlyCompleted.keys()) {
          if (!entriesBySession.value.has(sessionId)) continue
          scheduleDebouncedRefresh(sessionId)
        }
      }
    )
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
