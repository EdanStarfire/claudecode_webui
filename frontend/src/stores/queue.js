import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

/**
 * Queue Store - Manages message queue state per session (Issue #500)
 */
export const useQueueStore = defineStore('queue', () => {
  // ========== STATE ==========

  // Per-session queue items: Map<sessionId, Array<QueueItem>>
  const queuesBySession = ref(new Map())

  // Per-session pause state: Map<sessionId, boolean>
  const pausedBySession = ref(new Map())

  // Per-session pagination state: Map<sessionId, { offset, hasMore, total, pendingCount }>
  const paginationBySession = ref(new Map())

  // ========== GETTERS ==========

  function getItems(sessionId) {
    return queuesBySession.value.get(sessionId) || []
  }

  function getPendingCount(sessionId) {
    return paginationBySession.value.get(sessionId)?.pendingCount
      ?? getItems(sessionId).filter(i => i.status === 'pending').length
  }

  function isPaused(sessionId) {
    return pausedBySession.value.get(sessionId) || false
  }

  function hasMore(sessionId) {
    return paginationBySession.value.get(sessionId)?.hasMore || false
  }

  // ========== ACTIONS ==========

  async function fetchQueue(sessionId) {
    try {
      const data = await api.get(`/api/sessions/${sessionId}/queue?limit=100&offset=0`)
      queuesBySession.value.set(sessionId, data.items || [])
      queuesBySession.value = new Map(queuesBySession.value)
      paginationBySession.value.set(sessionId, {
        offset: data.items?.length || 0,
        hasMore: data.has_more || false,
        total: data.total || 0,
        pendingCount: data.pending_count ?? null,
      })
      paginationBySession.value = new Map(paginationBySession.value)
      return data.items
    } catch (error) {
      console.error('Failed to fetch queue:', error)
      return []
    }
  }

  async function loadMore(sessionId) {
    const pagination = paginationBySession.value.get(sessionId)
    if (!pagination?.hasMore) return
    try {
      const { offset } = pagination
      const data = await api.get(`/api/sessions/${sessionId}/queue?limit=100&offset=${offset}`)
      const existing = queuesBySession.value.get(sessionId) || []
      const combined = [...existing, ...(data.items || [])]
      queuesBySession.value.set(sessionId, combined)
      queuesBySession.value = new Map(queuesBySession.value)
      paginationBySession.value.set(sessionId, {
        offset: offset + (data.items?.length || 0),
        hasMore: data.has_more || false,
        total: data.total || 0,
        pendingCount: data.pending_count ?? null,
      })
      paginationBySession.value = new Map(paginationBySession.value)
      return data.items
    } catch (error) {
      console.error('Failed to load more queue items:', error)
      return []
    }
  }

  async function enqueueMessage(sessionId, content, resetSession = null, metadata = null) {
    try {
      const body = { content }
      if (resetSession !== null) body.reset_session = resetSession
      if (metadata !== null) body.metadata = metadata

      const data = await api.post(`/api/sessions/${sessionId}/queue-message`, body)
      return data.item
    } catch (error) {
      console.error('Failed to enqueue message:', error)
      throw error
    }
  }

  async function cancelItem(sessionId, queueId) {
    try {
      const data = await api.delete(`/api/sessions/${sessionId}/queue/${queueId}`)
      return data.item
    } catch (error) {
      console.error('Failed to cancel queue item:', error)
      throw error
    }
  }

  async function requeueItem(sessionId, queueId) {
    try {
      const data = await api.post(`/api/sessions/${sessionId}/queue/${queueId}/requeue`)
      return data.item
    } catch (error) {
      console.error('Failed to requeue item:', error)
      throw error
    }
  }

  async function clearQueue(sessionId) {
    try {
      const data = await api.delete(`/api/sessions/${sessionId}/queue`)
      return data.cancelled_count
    } catch (error) {
      console.error('Failed to clear queue:', error)
      throw error
    }
  }

  async function pauseQueue(sessionId, paused) {
    try {
      const data = await api.put(`/api/sessions/${sessionId}/queue/pause`, { paused })
      pausedBySession.value.set(sessionId, paused)
      pausedBySession.value = new Map(pausedBySession.value)
      return data
    } catch (error) {
      console.error('Failed to pause/resume queue:', error)
      throw error
    }
  }

  /**
   * Handle queue_update WebSocket message.
   * Called from websocket store when a queue_update message arrives.
   */
  function handleQueueUpdate(sessionId, payload) {
    const action = payload.action

    // Re-fetch from page 1 to stay current with real-time changes
    fetchQueue(sessionId)

    // Update pause state from action
    if (action === 'paused') {
      pausedBySession.value.set(sessionId, true)
      pausedBySession.value = new Map(pausedBySession.value)
    } else if (action === 'resumed') {
      pausedBySession.value.set(sessionId, false)
      pausedBySession.value = new Map(pausedBySession.value)
    }
  }

  /**
   * Update pause state from session data (e.g., from state_change).
   */
  function updatePauseState(sessionId, paused) {
    pausedBySession.value.set(sessionId, paused)
    pausedBySession.value = new Map(pausedBySession.value)
  }

  return {
    // State
    queuesBySession,
    pausedBySession,
    paginationBySession,
    // Getters
    getItems,
    getPendingCount,
    isPaused,
    hasMore,
    // Actions
    fetchQueue,
    loadMore,
    enqueueMessage,
    cancelItem,
    requeueItem,
    clearQueue,
    pauseQueue,
    handleQueueUpdate,
    updatePauseState,
  }
})
