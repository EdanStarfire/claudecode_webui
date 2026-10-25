import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'

/**
 * Background Tasks Store (issue #2145) — per-session latest snapshot of running
 * background tasks (bash / agent / workflow), `[{task_id, task_type, description}]`.
 *
 * Each `background_tasks_changed` event carries the FULL current list, so setTasks
 * replaces. Fed ONLY by live events and the backend snapshot endpoint — never by
 * history replay (a process death emits no empty list, so old events are stale).
 */
export const useBackgroundTasksStore = defineStore('backgroundTasks', () => {
  const tasksBySession = ref(new Map())

  /** Tolerant normalization: bad entries (no task_id) skipped, never throws. */
  function normalize(tasks) {
    if (!Array.isArray(tasks)) return []
    return tasks
      .filter(t => t && typeof t === 'object' && t.task_id)
      .map(t => ({
        task_id: String(t.task_id),
        task_type: String(t.task_type || ''),
        description: String(t.description || ''),
      }))
  }

  function setTasks(sessionId, tasks) {
    if (!sessionId) return
    const next = new Map(tasksBySession.value)
    const normalized = normalize(tasks)
    if (normalized.length === 0) next.delete(sessionId)
    else next.set(sessionId, normalized)
    tasksBySession.value = next
  }

  function clear(sessionId) {
    if (!tasksBySession.value.has(sessionId)) return
    const next = new Map(tasksBySession.value)
    next.delete(sessionId)
    tasksBySession.value = next
  }

  async function hydrate(sessionId) {
    try {
      const data = await api.get(`/api/sessions/${sessionId}/background_tasks`)
      setTasks(sessionId, data.tasks)
    } catch (error) {
      console.warn(`Failed to hydrate background tasks for session ${sessionId}:`, error)
    }
  }

  function tasksForSession(sessionId) {
    return tasksBySession.value.get(sessionId) || []
  }

  const currentTasks = computed(() => tasksForSession(useSessionStore().currentSessionId))
  const currentCount = computed(() => currentTasks.value.length)

  return { tasksBySession, setTasks, clear, hydrate, tasksForSession, currentTasks, currentCount }
})
