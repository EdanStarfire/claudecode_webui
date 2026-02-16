import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

/**
 * Schedule Store - Manages cron-based schedule state (Issue #495)
 *
 * Handles:
 * - Schedule CRUD operations per legion
 * - Schedule count per minion (for badge display)
 * - WebSocket-driven real-time updates
 */
export const useScheduleStore = defineStore('schedule', () => {
  // ========== STATE ==========

  // Schedules per legion (legionId -> Schedule[])
  const schedulesByLegion = ref(new Map())

  // Active schedule count per minion (minionId -> number) for badge display
  const scheduleCountByMinion = ref(new Map())

  // Currently selected schedule ID (for detail view)
  const selectedScheduleId = ref(null)

  // Execution history for the selected schedule
  const executionHistory = ref([])

  // ========== COMPUTED ==========

  /**
   * Get schedules for a specific legion
   */
  function getSchedules(legionId) {
    return schedulesByLegion.value.get(legionId) || []
  }

  /**
   * Get active schedule count for a minion
   */
  function getScheduleCount(minionId) {
    return scheduleCountByMinion.value.get(minionId) || 0
  }

  // ========== ACTIONS ==========

  /**
   * Load schedules for a legion from the API
   */
  async function loadSchedules(legionId) {
    try {
      const data = await api.get(`/api/legions/${legionId}/schedules`)
      const schedules = data.schedules || []
      schedulesByLegion.value.set(legionId, schedules)
      _rebuildMinionCounts(legionId, null)
      return schedules
    } catch (error) {
      console.error('Failed to load schedules:', error)
      return []
    }
  }

  /**
   * Create a new schedule
   */
  async function createSchedule(legionId, scheduleData) {
    const data = await api.post(`/api/legions/${legionId}/schedules`, scheduleData)
    const schedule = data.schedule
    _upsertSchedule(legionId, schedule)
    return schedule
  }

  /**
   * Update an existing schedule
   */
  async function updateSchedule(legionId, scheduleId, fields) {
    const data = await api.put(
      `/api/legions/${legionId}/schedules/${scheduleId}`,
      fields
    )
    const schedule = data.schedule
    _upsertSchedule(legionId, schedule)
    return schedule
  }

  /**
   * Pause a schedule
   */
  async function pauseSchedule(legionId, scheduleId) {
    const data = await api.post(
      `/api/legions/${legionId}/schedules/${scheduleId}/pause`
    )
    _upsertSchedule(legionId, data.schedule)
    return data.schedule
  }

  /**
   * Resume a schedule
   */
  async function resumeSchedule(legionId, scheduleId) {
    const data = await api.post(
      `/api/legions/${legionId}/schedules/${scheduleId}/resume`
    )
    _upsertSchedule(legionId, data.schedule)
    return data.schedule
  }

  /**
   * Cancel a schedule permanently
   */
  async function cancelSchedule(legionId, scheduleId) {
    const data = await api.post(
      `/api/legions/${legionId}/schedules/${scheduleId}/cancel`
    )
    _upsertSchedule(legionId, data.schedule)
    return data.schedule
  }

  /**
   * Delete a schedule
   */
  async function deleteSchedule(legionId, scheduleId) {
    await api.delete(`/api/legions/${legionId}/schedules/${scheduleId}`)
    _removeSchedule(legionId, scheduleId)
  }

  /**
   * Load execution history for a schedule
   */
  async function loadHistory(legionId, scheduleId, limit = 50, offset = 0) {
    try {
      const data = await api.get(
        `/api/legions/${legionId}/schedules/${scheduleId}/history?limit=${limit}&offset=${offset}`
      )
      executionHistory.value = data.executions || []
      return executionHistory.value
    } catch (error) {
      console.error('Failed to load schedule history:', error)
      executionHistory.value = []
      return []
    }
  }

  /**
   * Handle WebSocket schedule_updated event
   */
  function handleScheduleEvent(legionId, event) {
    if (!event || !event.schedule) return

    if (event.deleted) {
      _removeSchedule(legionId, event.schedule.schedule_id)
    } else {
      _upsertSchedule(legionId, event.schedule)
    }
  }

  // ========== INTERNAL HELPERS ==========

  function _upsertSchedule(legionId, schedule) {
    const schedules = schedulesByLegion.value.get(legionId) || []
    const idx = schedules.findIndex(s => s.schedule_id === schedule.schedule_id)
    if (idx >= 0) {
      schedules[idx] = schedule
    } else {
      schedules.push(schedule)
    }
    schedulesByLegion.value.set(legionId, [...schedules])
    _rebuildMinionCounts(legionId, null)
  }

  function _removeSchedule(legionId, scheduleId) {
    const schedules = schedulesByLegion.value.get(legionId) || []
    // Capture minion_ids referenced BEFORE removal (to clear stale counts)
    const minionIdsBefore = new Set(schedules.map(s => s.minion_id))
    const filtered = schedules.filter(s => s.schedule_id !== scheduleId)
    schedulesByLegion.value.set(legionId, filtered)
    _rebuildMinionCounts(legionId, minionIdsBefore)

    if (selectedScheduleId.value === scheduleId) {
      selectedScheduleId.value = null
      executionHistory.value = []
    }
  }

  function _rebuildMinionCounts(legionId, previousMinionIds) {
    const schedules = schedulesByLegion.value.get(legionId) || []
    const counts = new Map()

    for (const s of schedules) {
      if (s.status === 'active') {
        counts.set(s.minion_id, (counts.get(s.minion_id) || 0) + 1)
      }
    }

    // Include both current and previous minion IDs to clear stale entries
    const allMinionIds = new Set(schedules.map(s => s.minion_id))
    if (previousMinionIds) {
      for (const mid of previousMinionIds) {
        allMinionIds.add(mid)
      }
    }

    // Replace the entire Map to trigger Vue reactivity
    const newCounts = new Map(scheduleCountByMinion.value)
    for (const mid of allMinionIds) {
      if (counts.has(mid)) {
        newCounts.set(mid, counts.get(mid))
      } else {
        newCounts.delete(mid)
      }
    }
    scheduleCountByMinion.value = newCounts
  }

  return {
    // State
    schedulesByLegion,
    scheduleCountByMinion,
    selectedScheduleId,
    executionHistory,
    // Getters
    getSchedules,
    getScheduleCount,
    // Actions
    loadSchedules,
    createSchedule,
    updateSchedule,
    pauseSchedule,
    resumeSchedule,
    cancelSchedule,
    deleteSchedule,
    loadHistory,
    handleScheduleEvent,
  }
})
