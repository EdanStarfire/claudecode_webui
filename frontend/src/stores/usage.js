import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { api } from '@/utils/api'

export const useUsageStore = defineStore('usage', () => {
  // ========== STATE ==========

  // Usage aggregate per session (sessionId -> usageDict)
  const usageBySession = ref(new Map())

  // ========== COMPUTED ==========

  const currentUsage = computed(() => {
    const sessionStore = useSessionStore()
    return usageBySession.value.get(sessionStore.currentSessionId) || null
  })

  // ========== ACTIONS ==========

  async function loadUsage(sessionId) {
    if (!sessionId) return
    try {
      const data = await api.get(`/api/sessions/${sessionId}/usage`)
      usageBySession.value.set(sessionId, data)
      usageBySession.value = new Map(usageBySession.value)
    } catch (err) {
      // 404 = no usage yet — silently ignore
      if (err?.status !== 404) {
        console.warn(`Failed to load usage for session ${sessionId}:`, err)
      }
    }
  }

  function handleUsageUpdated(event) {
    const sessionId = event.session_id
    const usage = event.usage
    if (!sessionId || !usage) return
    usageBySession.value.set(sessionId, usage)
    usageBySession.value = new Map(usageBySession.value)
  }

  function clearUsage(sessionId) {
    if (!sessionId) return
    usageBySession.value.delete(sessionId)
    usageBySession.value = new Map(usageBySession.value)
  }

  // ========== RETURN ==========
  return {
    usageBySession,
    currentUsage,
    loadUsage,
    handleUsageUpdated,
    clearUsage,
  }
})
