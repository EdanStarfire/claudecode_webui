import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../utils/api'

/**
 * Session Store - Manages session state and operations
 * Replaces the complex Map+Array dual storage from vanilla JS
 */
export const useSessionStore = defineStore('session', () => {
  // ========== STATE ==========

  // Single source of truth - no more Map + Array duplication!
  const sessions = ref(new Map())

  // Current selection
  const currentSessionId = ref(null)

  // Input cache per session (preserve text when switching)
  const inputCache = ref(new Map())

  // Session init data (for info modal)
  const initData = ref(new Map())

  // Deleting sessions tracking
  const deletingSessions = ref(new Set())

  // ========== COMPUTED ==========

  const currentSession = computed(() =>
    sessions.value.get(currentSessionId.value)
  )

  // Ordered sessions - automatically sorted by order property
  const orderedSessions = computed(() =>
    Array.from(sessions.value.values())
      .sort((a, b) => a.order - b.order)
  )

  // Sessions filtered by project
  const sessionsInProject = (projectId) => computed(() =>
    orderedSessions.value.filter(s => s.project_id === projectId)
  )

  // Current session's input text
  const currentInput = computed({
    get: () => inputCache.value.get(currentSessionId.value) || '',
    set: (value) => {
      if (currentSessionId.value) {
        inputCache.value.set(currentSessionId.value, value)
      }
    }
  })

  // ========== ACTIONS ==========

  /**
   * Fetch all sessions from backend
   */
  async function fetchSessions() {
    try {
      const data = await api.get('/api/sessions')

      // Clear and rebuild sessions map
      sessions.value.clear()
      data.sessions.forEach(session => {
        sessions.value.set(session.session_id, session)
      })

      console.log(`Loaded ${sessions.value.size} sessions`)
      return data.sessions
    } catch (error) {
      console.error('Failed to fetch sessions:', error)
      throw error
    }
  }

  /**
   * Create a new session
   */
  async function createSession(projectId, formData) {
    try {
      const response = await api.post('/api/sessions', {
        project_id: projectId,
        ...formData
      })

      const sessionId = response.session_id

      // Fetch the full session details
      const data = await api.get(`/api/sessions/${sessionId}`)

      // Extract session from response (API returns {session: {...}, sdk: {...}, storage: {...}})
      const session = data.session

      // Add to sessions map
      sessions.value.set(sessionId, session)

      // Trigger reactivity
      sessions.value = new Map(sessions.value)

      console.log(`Created session ${sessionId}`)
      return session
    } catch (error) {
      console.error('Failed to create session:', error)
      throw error
    }
  }

  /**
   * Select a session (navigate to it)
   */
  async function selectSession(sessionId) {
    // Don't re-select if already current
    if (currentSessionId.value === sessionId) {
      return
    }

    // Update current session
    currentSessionId.value = sessionId

    // Get the session to check its state
    const session = sessions.value.get(sessionId)

    // Auto-start sessions in created or terminated states
    if (session && (session.state === 'created' || session.state === 'terminated')) {
      try {
        await startSession(sessionId)
        console.log(`Auto-started session ${sessionId} (was in ${session.state} state)`)
      } catch (error) {
        console.error(`Failed to auto-start session ${sessionId}:`, error)
        // Continue with selection even if start fails
      }
    }

    // Load messages for this session
    const messageStore = await import('./message')
    await messageStore.useMessageStore().loadMessages(sessionId)

    // Connect session websocket
    const wsStore = await import('./websocket')
    wsStore.useWebSocketStore().connectSession(sessionId)

    console.log(`Selected session ${sessionId}`)
  }

  /**
   * Update session data (called from WebSocket updates)
   */
  function updateSession(sessionId, updates) {
    const session = sessions.value.get(sessionId)
    if (session) {
      Object.assign(session, updates)

      // Trigger reactivity
      sessions.value = new Map(sessions.value)
    }
  }

  /**
   * Delete a session
   */
  async function deleteSession(sessionId) {
    deletingSessions.value.add(sessionId)

    try {
      await api.delete(`/api/sessions/${sessionId}`)

      // Remove from maps
      sessions.value.delete(sessionId)
      inputCache.value.delete(sessionId)
      initData.value.delete(sessionId)

      // Trigger reactivity
      sessions.value = new Map(sessions.value)

      // If deleted current session, clear selection
      if (currentSessionId.value === sessionId) {
        currentSessionId.value = null
        const router = useRouter()
        router.push('/')
      }

      console.log(`Deleted session ${sessionId}`)
    } catch (error) {
      console.error('Failed to delete session:', error)
      throw error
    } finally {
      deletingSessions.value.delete(sessionId)
    }
  }

  /**
   * Update session name
   */
  async function updateSessionName(sessionId, name) {
    try {
      await api.put(`/api/sessions/${sessionId}/name`, { name })
      updateSession(sessionId, { name })
      console.log(`Updated session ${sessionId} name to "${name}"`)
    } catch (error) {
      console.error('Failed to update session name:', error)
      throw error
    }
  }

  /**
   * Set permission mode for session
   */
  async function setPermissionMode(sessionId, mode) {
    try {
      await api.post(`/api/sessions/${sessionId}/permission-mode`, { mode })
      updateSession(sessionId, { current_permission_mode: mode })
      console.log(`Set permission mode for session ${sessionId} to ${mode}`)
    } catch (error) {
      console.error('Failed to set permission mode:', error)
      throw error
    }
  }

  /**
   * Start a session
   */
  async function startSession(sessionId) {
    try {
      await api.post(`/api/sessions/${sessionId}/start`)
      console.log(`Started session ${sessionId}`)
    } catch (error) {
      console.error('Failed to start session:', error)
      throw error
    }
  }

  /**
   * Pause a session
   */
  async function pauseSession(sessionId) {
    try {
      await api.post(`/api/sessions/${sessionId}/pause`)
      console.log(`Paused session ${sessionId}`)
    } catch (error) {
      console.error('Failed to pause session:', error)
      throw error
    }
  }

  /**
   * Terminate a session
   */
  async function terminateSession(sessionId) {
    try {
      await api.post(`/api/sessions/${sessionId}/terminate`)
      console.log(`Terminated session ${sessionId}`)
    } catch (error) {
      console.error('Failed to terminate session:', error)
      throw error
    }
  }

  /**
   * Get session by ID
   */
  function getSession(sessionId) {
    return sessions.value.get(sessionId)
  }

  /**
   * Store session init data (from init message)
   */
  function storeInitData(sessionId, data) {
    initData.value.set(sessionId, data)
  }

  // ========== RETURN ==========
  return {
    // State
    sessions,
    currentSessionId,
    inputCache,
    initData,
    deletingSessions,

    // Computed
    currentSession,
    orderedSessions,
    sessionsInProject,
    currentInput,

    // Actions
    fetchSessions,
    createSession,
    selectSession,
    updateSession,
    deleteSession,
    updateSessionName,
    setPermissionMode,
    startSession,
    pauseSession,
    terminateSession,
    getSession,
    storeInitData
  }
})
