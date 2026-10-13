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
   * Emergency halt all sessions in project (terminates all regardless of state).
   * Returns { stopped_session_ids, failed_sessions, total_sessions }.
   */
  async function haltAll(legionId) {
    try {
      const data = await api.post(`/api/legions/${legionId}/halt-all`)

      console.log(`Stopped ${data.stopped_session_ids?.length ?? 0} of ${data.total_sessions} sessions in project ${legionId}`)

      if (data.failed_sessions && data.failed_sessions.length > 0) {
        console.warn('Failed to stop some sessions:', data.failed_sessions)
      }

      return data
    } catch (error) {
      console.error('Failed to halt all sessions:', error)
      throw error
    }
  }

  /**
   * Resume exactly the sessions this project's Stop All/Stop Selected halted
   * (issue #2159 stage 6c's persisted fleet snapshot), not just "every ACTIVE
   * minion". Bulk, unbatched — ProjectOverview.vue's confirmResume() keeps its
   * own throttled per-session orchestration against getFleetSnapshot()/
   * clearFleetStopped() below for the configurable batch-size/delay UX; this is
   * the coarse-grained equivalent for any other caller.
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
   * Issue #2159 (stage 6c): current fleet halt/resume snapshot for a project —
   * { stopped_session_ids, processing_session_ids } — replacing
   * frontend/src/utils/stoppedSet.js's sessionStorage-only bookkeeping, which
   * didn't survive a browser reload or a Backend restart.
   */
  async function getFleetSnapshot(legionId) {
    return await api.get(`/api/legions/${legionId}/fleet-snapshot`)
  }

  /**
   * Merge newly-halted session ids into the persisted fleet snapshot — used by
   * Stop Selected, which halts sessions individually rather than through
   * halt-all (which records its own snapshot entries server-side).
   */
  async function recordFleetStopped(legionId, sessionIds, processingSessionIds = []) {
    return await api.post(`/api/legions/${legionId}/fleet-snapshot/record`, {
      session_ids: sessionIds,
      processing_session_ids: processingSessionIds,
    })
  }

  /**
   * Remove specific session ids from the persisted fleet snapshot — used once
   * each has been successfully resumed/started outside of resumeAll() above.
   */
  async function clearFleetStopped(legionId, sessionIds) {
    return await api.post(`/api/legions/${legionId}/fleet-snapshot/clear`, {
      session_ids: sessionIds,
    })
  }

  /**
   * Reparent a minion to a new parent within the same legion (user-initiated).
   *
   * @param {string} legionId - Project/legion ID
   * @param {string} subjectId - Session ID of the minion to move
   * @param {string|null} newParentId - Session ID of the new parent, or null for root
   */
  async function reparentMinion(legionId, subjectId, newParentId) {
    const response = await api.post(
      `/api/legions/${legionId}/minions/${subjectId}/reparent`,
      { new_parent_id: newParentId }
    )
    return response
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
    getFleetSnapshot,
    recordFleetStopped,
    clearFleetStopped,
    reparentMinion,
    clearLegionData
  }
})
