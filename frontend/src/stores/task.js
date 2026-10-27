import { defineStore } from 'pinia'
import { ref, computed, nextTick } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'
import { useUIStore } from './ui'

/**
 * Task Store - Manages SDK task system state per session
 *
 * The SDK task system (TaskCreate, TaskUpdate, TaskList, TaskGet) allows agents
 * to track progress on multi-step work. This store maintains task state
 * per session and integrates with the message store to update tasks
 * based on tool call results.
 */
export const useTaskStore = defineStore('task', () => {
  // ========== STATE ==========

  // Tasks per session (sessionId -> Map<taskId, Task>)
  // Task shape: { id, subject, description, status, activeForm, owner, blocks, blockedBy, metadata }
  const tasksBySession = ref(new Map())

  // Track which sessions have tasks (for conditional sidebar display)
  const sessionsWithTasks = ref(new Set())

  // ========== COMPUTED ==========

  /**
   * Get tasks for a specific session as a sorted array
   */
  function tasksForSession(sessionId) {
    const taskMap = tasksBySession.value.get(sessionId)
    if (!taskMap) return []

    // Convert to array, exclude deleted tasks, sort by creation order
    return Array.from(taskMap.values())
      .filter(t => t.status !== 'deleted')
      .sort((a, b) => {
        const idA = parseInt(a.id.replace(/\D/g, '')) || 0
        const idB = parseInt(b.id.replace(/\D/g, '')) || 0
        return idA - idB
      })
  }

  /**
   * Get the currently active (in_progress) task for a session
   */
  function activeTask(sessionId) {
    const tasks = tasksForSession(sessionId)
    return tasks.find(t => t.status === 'in_progress') || null
  }

  /**
   * Get task statistics for a session
   */
  function taskStats(sessionId) {
    const tasks = tasksForSession(sessionId)
    return {
      total: tasks.length,
      completed: tasks.filter(t => t.status === 'completed').length,
      inProgress: tasks.filter(t => t.status === 'in_progress').length,
      pending: tasks.filter(t => t.status === 'pending').length
    }
  }

  /**
   * Check if a session has any tasks
   */
  function hasTasks(sessionId) {
    return sessionsWithTasks.value.has(sessionId)
  }

  /**
   * Current session's tasks (computed)
   */
  const currentTasks = computed(() => {
    const sessionStore = useSessionStore()
    return tasksForSession(sessionStore.currentSessionId)
  })

  /**
   * Current session's active task (computed)
   */
  const currentActiveTask = computed(() => {
    const sessionStore = useSessionStore()
    return activeTask(sessionStore.currentSessionId)
  })

  /**
   * Current session's task stats (computed)
   */
  const currentTaskStats = computed(() => {
    const sessionStore = useSessionStore()
    return taskStats(sessionStore.currentSessionId)
  })

  /**
   * Check if current session has tasks (computed)
   */
  const currentHasTasks = computed(() => {
    const sessionStore = useSessionStore()
    return hasTasks(sessionStore.currentSessionId)
  })

  // ========== ACTIONS ==========

  /**
   * Create or update a task from TaskCreate tool result
   */
  function createTask(sessionId, task) {
    if (!tasksBySession.value.has(sessionId)) {
      tasksBySession.value.set(sessionId, new Map())
    }

    const taskMap = tasksBySession.value.get(sessionId)
    taskMap.set(task.id, {
      id: task.id,
      subject: task.subject || '',
      description: task.description || '',
      status: task.status || 'pending',
      activeForm: task.activeForm || null,
      owner: task.owner || null,
      blocks: task.blocks || [],
      blockedBy: task.blockedBy || [],
      metadata: task.metadata || {}
    })

    // Mark session as having tasks
    sessionsWithTasks.value.add(sessionId)

    // Trigger reactivity
    tasksBySession.value = new Map(tasksBySession.value)
    sessionsWithTasks.value = new Set(sessionsWithTasks.value)

    console.log(`Created/updated task ${task.id} for session ${sessionId}`)
  }

  /**
   * Update a task from TaskUpdate tool result
   */
  function updateTask(sessionId, taskId, updates) {
    const taskMap = tasksBySession.value.get(sessionId)
    if (!taskMap) {
      console.warn(`No tasks found for session ${sessionId}`)
      return
    }

    const task = taskMap.get(taskId)
    if (!task) {
      // Don't create a task if the update is a deletion
      if (updates.status === 'deleted') return
      // Task doesn't exist yet - create it with updates
      createTask(sessionId, { id: taskId, ...updates })
      return
    }

    // Apply updates
    if (updates.subject !== undefined) task.subject = updates.subject
    if (updates.description !== undefined) task.description = updates.description
    if (updates.status !== undefined) task.status = updates.status
    if (updates.activeForm !== undefined) task.activeForm = updates.activeForm
    if (updates.owner !== undefined) task.owner = updates.owner
    if (updates.metadata !== undefined) {
      task.metadata = { ...task.metadata, ...updates.metadata }
    }

    // Handle blocks/blockedBy additions
    if (updates.addBlocks) {
      task.blocks = [...new Set([...task.blocks, ...updates.addBlocks])]
    }
    if (updates.addBlockedBy) {
      task.blockedBy = [...new Set([...task.blockedBy, ...updates.addBlockedBy])]
    }

    // Handle status === 'deleted' - remove the task
    if (updates.status === 'deleted') {
      taskMap.delete(taskId)

      // Check if session still has tasks
      if (taskMap.size === 0) {
        sessionsWithTasks.value.delete(sessionId)
        sessionsWithTasks.value = new Set(sessionsWithTasks.value)
      }
    }

    // Trigger reactivity
    tasksBySession.value = new Map(tasksBySession.value)

    console.log(`Updated task ${taskId} for session ${sessionId}`, updates)
  }

  /**
   * Get a specific task by ID
   */
  function getTask(sessionId, taskId) {
    return tasksBySession.value.get(sessionId)?.get(taskId) || null
  }

  /**
   * Clear all tasks for a session (for session reset)
   */
  function clearTasks(sessionId) {
    _liveBuffers.delete(sessionId)
    _hydrationSeq.set(sessionId, (_hydrationSeq.get(sessionId) || 0) + 1) // outlive any in-flight snapshot
    if (hydrationStateBySession.value.has(sessionId)) _setHydrationState(sessionId, null)
    tasksBySession.value.delete(sessionId)
    sessionsWithTasks.value.delete(sessionId)

    // Trigger reactivity
    tasksBySession.value = new Map(tasksBySession.value)
    sessionsWithTasks.value = new Set(sessionsWithTasks.value)

    console.log(`Cleared tasks for session ${sessionId}`)
  }

  // ========== SERVER SNAPSHOT (Issue #2030, stage 1b) ==========
  // The Tasks panel is hydrated from GET /api/sessions/{id}/tasks (reduced server-side from the
  // whole history), never from the loaded message window — a tail-first load only holds a suffix
  // of the history, so replaying it would produce partial or subject-less tasks. History replay
  // (load / backfill / resync / archive) therefore never calls handleTaskToolResult(); only live
  // records do.

  // sessionId -> 'loading' | 'ready' | 'error'. Absent == nothing requested yet.
  const hydrationStateBySession = ref(new Map())
  // Live task results that arrive while a snapshot is outstanding are queued here and replayed on
  // top of it once applied (the reducer is idempotent), so a snapshot can never revert a newer
  // live update. Non-reactive bookkeeping. Map<sessionId, Array<[toolName, input, result]>>
  const _liveBuffers = new Map()
  // Newest in-flight hydrate() request per session (see hydrate()).
  const _hydrationSeq = new Map()
  // Overlapping snapshot applications share one save/restore of the auto-show suppress flag.
  let _suppressDepth = 0
  let _suppressRestore = false

  function _setHydrationState(sessionId, state) {
    const next = new Map(hydrationStateBySession.value)
    if (state) next.set(sessionId, state)
    else next.delete(sessionId)
    hydrationStateBySession.value = next
  }

  /** Starts buffering live task results for `sessionId` (called when the history window is applied). */
  function beginHydration(sessionId) {
    if (!_liveBuffers.has(sessionId)) _liveBuffers.set(sessionId, [])
    if (hydrationStateBySession.value.get(sessionId) !== 'loading') _setHydrationState(sessionId, 'loading')
  }

  /**
   * Replaces `sessionId`'s tasks with the server snapshot, then replays any live results that
   * arrived while it was in flight. A failed request leaves the panel in an explicit 'error' state
   * (retry via another hydrate()); it never falls back to window-derived data.
   */
  async function hydrate(sessionId) {
    beginHydration(sessionId)
    // Only the newest request for a session may apply its snapshot and drain the live buffer;
    // an older one finishing late (overlapping chain / resync / error reload) or one outlived
    // by clearTasks() is stale and dropped.
    const seq = (_hydrationSeq.get(sessionId) || 0) + 1
    _hydrationSeq.set(sessionId, seq)
    const isStale = () => _hydrationSeq.get(sessionId) !== seq
    try {
      const data = await api.get(`/api/sessions/${sessionId}/tasks`)
      if (isStale()) return
      const taskMap = new Map()
      for (const task of data.tasks || []) {
        taskMap.set(String(task.id), {
          id: String(task.id),
          subject: task.subject || '',
          description: task.description || '',
          status: task.status || 'pending',
          activeForm: task.activeForm || null,
          owner: task.owner || null,
          blocks: task.blocks || [],
          blockedBy: task.blockedBy || [],
          metadata: task.metadata || {},
        })
      }
      const tasks = new Map(tasksBySession.value)
      const withTasks = new Set(sessionsWithTasks.value)
      if (taskMap.size > 0) {
        tasks.set(sessionId, taskMap)
        withTasks.add(sessionId)
      } else {
        tasks.delete(sessionId)
        withTasks.delete(sessionId)
      }
      // A snapshot landing is "loading existing data", not "tasks just appeared": it must not
      // pop the right panel open (App.vue auto-shows on the 0 -> N transition of live task
      // creation). The suppress flag only guards the watcher flush this assignment triggers.
      const uiStore = useUIStore()
      if (_suppressDepth++ === 0) _suppressRestore = uiStore.suppressAutoShow
      uiStore.setSuppressAutoShow(true)
      try {
        tasksBySession.value = tasks
        sessionsWithTasks.value = withTasks
        _setHydrationState(sessionId, 'ready')
        await nextTick()
      } finally {
        if (--_suppressDepth === 0) uiStore.setSuppressAutoShow(_suppressRestore)
      }
    } catch (error) {
      if (isStale()) return
      console.warn(`Failed to hydrate tasks for session ${sessionId}:`, error)
      _setHydrationState(sessionId, 'error')
    } finally {
      if (isStale()) return
      const buffered = _liveBuffers.get(sessionId) || []
      _liveBuffers.delete(sessionId)
      for (const [toolName, input, result] of buffered) handleTaskToolResult(sessionId, toolName, input, result)
    }
  }

  /** True while a snapshot is outstanding (the panel shows a brief "loading tasks…"). */
  function isHydrating(sessionId) {
    return hydrationStateBySession.value.get(sessionId) === 'loading'
  }

  /** True when the last snapshot request failed. */
  function hasHydrationError(sessionId) {
    return hydrationStateBySession.value.get(sessionId) === 'error'
  }

  /**
   * Handle task tool result to update task state
   * Called from message store when task tools complete
   */
  function handleTaskToolResult(sessionId, toolName, input, result) {
    // Issue #2030: a snapshot is outstanding — queue, replayed on top of it in hydrate().
    const buffer = _liveBuffers.get(sessionId)
    if (buffer) {
      buffer.push([toolName, input, result])
      return
    }
    // Skip if result is an error
    if (result?.error) {
      console.log(`Skipping task update - tool ${toolName} returned error`)
      return
    }

    const resultContent = parseResultContent(result)
    if (!resultContent) return

    switch (toolName) {
      case 'TaskCreate': {
        // Result contains the created task with its ID
        // Parse the result which is typically a string like "Task #1 created successfully: subject"
        const taskId = extractTaskId(resultContent) || input?.id
        if (taskId) {
          createTask(sessionId, {
            id: taskId,
            subject: input?.subject || '',
            description: input?.description || '',
            status: 'pending',
            activeForm: input?.activeForm || null,
            owner: input?.owner || null,
            blocks: [],
            blockedBy: [],
            metadata: input?.metadata || {}
          })
        }
        break
      }

      case 'TaskUpdate': {
        const taskId = input?.taskId
        if (taskId) {
          updateTask(sessionId, taskId, {
            status: input?.status,
            subject: input?.subject,
            description: input?.description,
            activeForm: input?.activeForm,
            owner: input?.owner,
            metadata: input?.metadata,
            addBlocks: input?.addBlocks,
            addBlockedBy: input?.addBlockedBy
          })
        }
        break
      }

      case 'TaskList': {
        // TaskList returns a summary of all tasks
        // We can use this to verify/sync state, but primary updates come from Create/Update
        // Parse the result to extract task summaries if needed
        break
      }

      case 'TaskGet': {
        // TaskGet returns full task details
        // We can use this to update cached task details
        const taskId = input?.taskId
        if (taskId && resultContent) {
          // Parse result for task details and update
          // This is typically used for display, not state mutation
        }
        break
      }
    }
  }

  /**
   * Parse result content from tool result
   */
  function parseResultContent(result) {
    if (!result) return null

    if (typeof result === 'string') return result
    if (result.content) {
      return typeof result.content === 'string' ? result.content : JSON.stringify(result.content)
    }
    if (result.message) return result.message

    return JSON.stringify(result)
  }

  /**
   * Extract task ID from result string
   * Handles formats like "Task #1 created successfully" or "Updated task #1 status"
   */
  function extractTaskId(resultString) {
    if (!resultString) return null

    // Match patterns like "Task #1" or "task #1"
    const match = resultString.match(/[Tt]ask\s*#(\d+)/i)
    if (match) return match[1]

    // Also try to match just "#1" at the start or after common words
    const simpleMatch = resultString.match(/#(\d+)/)
    if (simpleMatch) return simpleMatch[1]

    return null
  }

  // ========== RETURN ==========
  return {
    // State
    tasksBySession,
    sessionsWithTasks,

    // Computed
    currentTasks,
    currentActiveTask,
    currentTaskStats,
    currentHasTasks,

    // Getters (functions)
    tasksForSession,
    activeTask,
    taskStats,
    hasTasks,
    getTask,

    // Actions
    createTask,
    updateTask,
    clearTasks,
    handleTaskToolResult,
    // Issue #2030: server snapshot
    hydrate,
    beginHydration,
    isHydrating,
    hasHydrationError,
    hydrationStateBySession
  }
})
