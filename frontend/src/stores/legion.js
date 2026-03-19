import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

/**
 * Legion Store - Manages Legion multi-agent system state
 *
 * Handles:
 * - Comms (communications between minions/user)
 * - Minions (agent sessions within a legion)
 */
export const useLegionStore = defineStore('legion', () => {
  // ========== STATE ==========

  // Minions per legion (legionId -> Minion[])
  const minionsByLegion = ref(new Map())

  // ========== COMPUTED ==========

  // Current legion's minions (no currentLegionId - removed with WebSocket)
  const currentMinions = computed(() => {
    return []
  })

  // ========== ACTIONS ==========

  /**
   * Send a comm to a legion
   */
  async function sendComm(legionId, commData) {
    try {
      const response = await api.post(`/api/legions/${legionId}/comms`, commData)
      const comm = response.comm

      // Don't add to local state here - let the WebSocket broadcast handle it
      // This prevents duplicate entries when the backend broadcasts the comm back

      console.log(`Sent comm ${comm.comm_id} to legion ${legionId}`)
      return comm
    } catch (error) {
      console.error('Failed to send comm:', error)
      throw error
    }
  }

  /**
   * Load minions for a legion
   *
   * Note: Minions are just sessions that belong to a multi-agent project.
   * We don't need a separate API call - they're already in the session store
   * via the project's session_ids list.
   */
  async function loadMinions(legionId) {
    // Minions are loaded automatically via the session store when projects load
    // This function is kept for API compatibility but doesn't need to do anything
    console.log(`Minions for legion ${legionId} are already available in session store`)
    return []
  }

  /**
   * Create a minion in a legion
   */
  async function createMinion(legionId, minionData) {
    try {
      const response = await api.post(`/api/legions/${legionId}/minions`, minionData)
      const minion = response.minion

      // Refresh minions list
      await loadMinions(legionId)

      console.log(`Created minion ${minion.minion_id} in legion ${legionId}`)
      return minion
    } catch (error) {
      console.error('Failed to create minion:', error)
      throw error
    }
  }

  /**
   * Emergency halt all minions in legion
   */
  async function haltAll(legionId) {
    try {
      const data = await api.post(`/api/legions/${legionId}/halt-all`)

      console.log(`Halted ${data.halted_count} of ${data.total_minions} minions in legion ${legionId}`)

      if (data.failed_minions && data.failed_minions.length > 0) {
        console.warn('Failed to halt some minions:', data.failed_minions)
      }

      return data
    } catch (error) {
      console.error('Failed to halt all minions:', error)
      throw error
    }
  }

  /**
   * Resume all minions in legion
   */
  async function resumeAll(legionId) {
    try {
      const data = await api.post(`/api/legions/${legionId}/resume-all`)

      console.log(`Resumed ${data.resumed_count} of ${data.total_minions} minions in legion ${legionId}`)

      if (data.failed_minions && data.failed_minions.length > 0) {
        console.warn('Failed to resume some minions:', data.failed_minions)
      }

      return data
    } catch (error) {
      console.error('Failed to resume all minions:', error)
      throw error
    }
  }

  /**
   * Clear all legion data
   */
  function clearLegionData(legionId) {
    minionsByLegion.value.delete(legionId)

    // Trigger reactivity
    minionsByLegion.value = new Map(minionsByLegion.value)
  }

  // ========== RETURN ==========
  return {
    // State
    minionsByLegion,

    // Computed
    currentMinions,

    // Actions
    sendComm,
    loadMinions,
    createMinion,
    haltAll,
    resumeAll,
    clearLegionData
  }
})
