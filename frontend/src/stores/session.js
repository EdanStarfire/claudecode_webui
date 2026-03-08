import { defineStore } from 'pinia'
import { ref, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../utils/api'
import { useUIStore } from './ui'

// Module-level scroll position getter (only one MessageList instance exists at a time)
let _scrollPositionGetter = null

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

  // Attachment cache per session (preserve file attachments when switching)
  const attachmentCache = ref(new Map())

  // Session init data (for info modal)
  const initData = ref(new Map())

  // Deleting sessions tracking
  const deletingSessions = ref(new Set())

  // Ghost agents (deleted agents opened for archive browsing, transient)
  const ghostAgents = ref(new Map())

  // Last viewed archive per session (so chip clicks restore archive view)
  const lastViewedArchive = ref(new Map())

  // Scroll position preservation across session switches
  const scrollPositions = ref(new Map())            // sessionId → { index, isAtBottom }
  const pendingScrollRestoreSessionId = ref(null)   // session awaiting scroll restoration

  // Session reset counter (triggers archive re-fetch in AgentOverview)
  const sessionResets = ref(new Map())              // sessionId → reset count
  // Archive change counter (triggers archive re-fetch after erasure)
  const archiveChanges = ref(new Map())             // sessionId → change count

  // Session selection state (prevents concurrent selectSession calls)
  const selectingSession = ref(false)
  let pendingSelectAbort = null  // AbortController for current selection

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

  // Current session's file attachments
  const currentAttachments = computed({
    get: () => attachmentCache.value.get(currentSessionId.value) || [],
    set: (value) => {
      if (currentSessionId.value) {
        if (value.length === 0) {
          attachmentCache.value.delete(currentSessionId.value)
        } else {
          attachmentCache.value.set(currentSessionId.value, value)
        }
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
   * Now with abort mechanism to prevent concurrent selections
   */
  async function selectSession(sessionId) {
    // Don't re-select if already current AND not currently selecting another
    if (currentSessionId.value === sessionId && !selectingSession.value) {
      return
    }

    // Abort any pending selection
    if (pendingSelectAbort) {
      console.log(`Aborting pending selection to switch to ${sessionId}`)
      pendingSelectAbort.abort()
      pendingSelectAbort = null
    }

    // Create new abort controller for this selection
    const abortController = new AbortController()
    pendingSelectAbort = abortController

    // Set mutex flag
    selectingSession.value = true

    // Suppress auto-show of right panel while loading existing session data (#521)
    const uiStore = useUIStore()
    uiStore.setSuppressAutoShow(true)

    try {
      // Save outgoing session's scroll position
      const leavingSessionId = currentSessionId.value
      if (leavingSessionId && _scrollPositionGetter) {
        const pos = _scrollPositionGetter()
        if (pos !== null) {
          scrollPositions.value.set(leavingSessionId, pos)
        }
      }

      // Set pending restore for incoming session (before currentSessionId changes)
      if (scrollPositions.value.has(sessionId)) {
        pendingScrollRestoreSessionId.value = sessionId
      }

      // Update current session immediately to prevent UI confusion
      currentSessionId.value = sessionId

      // Check if aborted before continuing
      if (abortController.signal.aborted) {
        console.log(`Selection of ${sessionId} aborted early`)
        return
      }

      // Get the session to check its state
      let session = sessions.value.get(sessionId)

      // If session not in store (e.g., deeplink), fetch it first
      if (!session) {
        try {
          const data = await api.get(`/api/sessions/${sessionId}`)

          // Check abort after await
          if (abortController.signal.aborted) {
            console.log(`Selection of ${sessionId} aborted after fetch`)
            return
          }

          session = data.session
          sessions.value.set(sessionId, session)
          // Trigger reactivity
          sessions.value = new Map(sessions.value)
          console.log(`Fetched session ${sessionId} for deeplink`)
        } catch (error) {
          console.error(`Failed to fetch session ${sessionId}:`, error)
          // Can't proceed without session data
          return
        }
      }

      // Ephemeral sessions (managed by schedules) should never be auto-started by the UI.
      // Redirect to the latest archive if one exists, or show an informational view.
      if (session && session.is_ephemeral &&
          (session.state === 'created' || session.state === 'terminated')) {
        // Resolve project_id for the archive API
        const projectId = session.project_id
        if (projectId) {
          try {
            const archiveData = await api.get(`/api/projects/${projectId}/archives/${sessionId}`)

            // Check abort after await
            if (abortController.signal.aborted) return

            const archives = archiveData.archives || []
            if (archives.length > 0) {
              // Navigate to the latest archive
              const latest = archives[archives.length - 1]
              const router = useRouter()
              router.push(`/session/${sessionId}/archive/${latest.archive_id}`)
              return
            }
          } catch (error) {
            console.warn(`Failed to fetch archives for ephemeral session ${sessionId}:`, error)
          }
        }

        // No archives (or no project_id / fetch failed): show informational view
        // Set currentSessionId but skip auto-start entirely
        const messageStore = await import('./message')
        await messageStore.useMessageStore().loadMessages(sessionId)

        if (abortController.signal.aborted) return

        const resourceStore = await import('./resource')
        await resourceStore.useResourceStore().loadResources(sessionId)

        if (abortController.signal.aborted) return

        const wsStore = await import('./websocket')
        await wsStore.useWebSocketStore().connectSession(sessionId)

        if (!abortController.signal.aborted) {
          console.log(`Selected ephemeral session ${sessionId} (no auto-start)`)
        }
        return
      }

      // Auto-start sessions in created or terminated states
      if (session && (session.state === 'created' || session.state === 'terminated')) {
        try {
          await startSession(sessionId)

          // Check abort after start
          if (abortController.signal.aborted) {
            console.log(`Selection of ${sessionId} aborted after start`)
            return
          }

          console.log(`Auto-started session ${sessionId} (was in ${session.state} state)`)

          // Wait for session to start before connecting WebSocket
          // Poll for state change with timeout to avoid race condition
          // SDK can take 20-30 seconds to start, so wait up to 60 seconds
          const maxWaitMs = 60000
          const pollIntervalMs = 200
          const logIntervalMs = 5000
          let elapsedMs = 0
          let lastLogMs = 0

          while (elapsedMs < maxWaitMs) {
            // Check abort in polling loop
            if (abortController.signal.aborted) {
              console.log(`Selection of ${sessionId} aborted during state wait`)
              return
            }

            const currentSession = sessions.value.get(sessionId)
            // Only proceed when session is fully active (not just 'starting')
            // The 'starting' state means backend accepted the request, but SDK/WebSocket not ready yet
            if (currentSession && currentSession.state === 'active') {
              console.log(`Session ${sessionId} is now active after ${elapsedMs}ms`)
              break
            }

            // Issue #517: Break early if session enters error state (startup failure)
            // No point waiting 60s when the session has already failed
            if (currentSession && currentSession.state === 'error') {
              console.log(`Session ${sessionId} entered error state after ${elapsedMs}ms, stopping wait`)
              break
            }

            // Log progress every 5 seconds
            if (elapsedMs - lastLogMs >= logIntervalMs) {
              const currentState = sessions.value.get(sessionId)?.state || 'unknown'
              console.log(`Waiting for session ${sessionId} to become active (current: ${currentState}, ${elapsedMs / 1000}s elapsed)`)
              lastLogMs = elapsedMs
            }

            await new Promise(resolve => setTimeout(resolve, pollIntervalMs))
            elapsedMs += pollIntervalMs
          }

          // If still not active after timeout, log warning but continue
          // (WebSocket retry logic will handle eventual connection)
          const finalSession = sessions.value.get(sessionId)
          if (finalSession && finalSession.state !== 'active') {
            console.warn(`Session ${sessionId} did not become active within ${maxWaitMs / 1000}s (state: ${finalSession.state}), continuing anyway`)
          }
        } catch (error) {
          console.error(`Failed to auto-start session ${sessionId}:`, error)
          // Continue with selection even if start fails
        }
      }

      // Final abort check before expensive operations
      if (abortController.signal.aborted) {
        console.log(`Selection of ${sessionId} aborted before message load`)
        return
      }

      // Load messages for this session
      const messageStore = await import('./message')
      await messageStore.useMessageStore().loadMessages(sessionId)

      // Check abort after message load
      if (abortController.signal.aborted) {
        console.log(`Selection of ${sessionId} aborted after message load`)
        return
      }

      // Issue #404: Load resources (images and files) for this session
      const resourceStore = await import('./resource')
      await resourceStore.useResourceStore().loadResources(sessionId)

      // Check abort after resource load
      if (abortController.signal.aborted) {
        console.log(`Selection of ${sessionId} aborted after resource load`)
        return
      }

      // CRITICAL: Await websocket connection to prevent race conditions
      const wsStore = await import('./websocket')
      await wsStore.useWebSocketStore().connectSession(sessionId)

      // Final check - only log success if not aborted
      if (!abortController.signal.aborted) {
        console.log(`Selected session ${sessionId}`)
      }
    } finally {
      // Clear mutex and pending abort if this is still the current operation
      if (pendingSelectAbort === abortController) {
        pendingSelectAbort = null
      }
      selectingSession.value = false

      // Clear pending scroll restore if this selection was aborted
      if (abortController.signal.aborted && pendingScrollRestoreSessionId.value === sessionId) {
        pendingScrollRestoreSessionId.value = null
      }

      // Clear suppression after Vue processes reactive updates from loaded data (#521)
      nextTick(() => uiStore.setSuppressAutoShow(false))
    }
  }

  /**
   * Update session data (called from WebSocket updates)
   * If session doesn't exist, add it (for newly created sessions)
   */
  function updateSession(sessionId, updates) {
    const session = sessions.value.get(sessionId)
    if (session) {
      // Update existing session
      Object.assign(session, updates)
    } else {
      // Add new session (e.g., from state_change broadcast after creation)
      sessions.value.set(sessionId, updates)
      console.log(`Added new session ${sessionId} via WebSocket update`)
    }

    // Trigger reactivity
    sessions.value = new Map(sessions.value)
  }

  /**
   * Delete a session (handles cascading deletes for child sessions)
   */
  async function deleteSession(sessionId) {
    deletingSessions.value.add(sessionId)

    try {
      const response = await api.delete(`/api/sessions/${sessionId}`)

      // Get list of all deleted session IDs (includes cascaded children)
      const deletedIds = response.deleted_session_ids || [sessionId]

      // Remove all deleted sessions from maps
      for (const deletedId of deletedIds) {
        sessions.value.delete(deletedId)
        inputCache.value.delete(deletedId)
        attachmentCache.value.delete(deletedId)
        initData.value.delete(deletedId)
        scrollPositions.value.delete(deletedId)
        if (pendingScrollRestoreSessionId.value === deletedId) {
          pendingScrollRestoreSessionId.value = null
        }
      }

      // Trigger reactivity
      sessions.value = new Map(sessions.value)

      // If deleted current session (or it was a cascaded child), clear selection
      if (deletedIds.includes(currentSessionId.value)) {
        currentSessionId.value = null
        // Navigation is handled by the component calling this function
      }

      console.log(`Deleted ${deletedIds.length} session(s): ${deletedIds.join(', ')}`)
      return deletedIds
    } catch (error) {
      console.error('Failed to delete session:', error)
      throw error
    } finally {
      deletingSessions.value.delete(sessionId)
    }
  }

  /**
   * Generic session update via PATCH endpoint
   * @param {string} sessionId - Session ID
   * @param {Object} updates - Fields to update (name, model, etc.)
   */
  async function patchSession(sessionId, updates) {
    try {
      await api.patch(`/api/sessions/${sessionId}`, updates)
      updateSession(sessionId, updates)
      console.log(`Updated session ${sessionId}:`, updates)
    } catch (error) {
      console.error('Failed to update session:', error)
      throw error
    }
  }

  /**
   * Set permission mode for session
   * Also syncs to initData for UI consistency
   */
  async function setPermissionMode(sessionId, mode) {
    try {
      await api.post(`/api/sessions/${sessionId}/permission-mode`, { mode })
      updateSession(sessionId, { current_permission_mode: mode })

      // Sync to initData so SessionInfoModal shows updated mode
      const storedInitData = initData.value.get(sessionId)
      if (storedInitData) {
        storedInitData.permissionMode = mode
        initData.value.set(sessionId, storedInitData)
      }

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
   * Also syncs permission mode to session state for UI consistency
   */
  function storeInitData(sessionId, data) {
    initData.value.set(sessionId, data)

    // Sync permission mode from init data to session state
    if (data.permissionMode) {
      updateSession(sessionId, { current_permission_mode: data.permissionMode })
    }
  }

  // ========== SCROLL POSITION ACTIONS ==========

  function registerScrollPositionGetter(fn) {
    _scrollPositionGetter = fn
  }

  function clearScrollRestorePending() {
    pendingScrollRestoreSessionId.value = null
  }

  // ========== SESSION RESET TRACKING ==========

  function recordSessionReset(sessionId) {
    const current = sessionResets.value.get(sessionId) || 0
    sessionResets.value.set(sessionId, current + 1)
  }

  // ========== HISTORY & ARCHIVE ACTIONS ==========

  async function eraseHistory(sessionId) {
    const response = await api.delete(`/api/sessions/${sessionId}/history`)
    return response
  }

  async function eraseArchives(sessionId) {
    const response = await api.delete(`/api/sessions/${sessionId}/archives`)
    // Bump archive change counter so AgentOverview re-fetches
    const current = archiveChanges.value.get(sessionId) || 0
    archiveChanges.value.set(sessionId, current + 1)
    return response
  }

  async function checkHistoryArchivesStatus(sessionId) {
    return await api.get(`/api/sessions/${sessionId}/history-archives-status`)
  }

  // ========== GHOST AGENT ACTIONS ==========

  function addGhostAgent(agentId, agentData) {
    ghostAgents.value.set(agentId, agentData)
  }

  function removeGhostAgent(agentId) {
    ghostAgents.value.delete(agentId)
  }

  // ========== RETURN ==========
  return {
    // State
    sessions,
    currentSessionId,
    inputCache,
    attachmentCache,
    initData,
    deletingSessions,
    ghostAgents,
    lastViewedArchive,
    scrollPositions,
    pendingScrollRestoreSessionId,
    sessionResets,
    archiveChanges,

    // Computed
    currentSession,
    orderedSessions,
    sessionsInProject,
    currentInput,
    currentAttachments,

    // Actions
    fetchSessions,
    createSession,
    selectSession,
    updateSession,
    deleteSession,
    patchSession,
    setPermissionMode,
    startSession,
    terminateSession,
    getSession,
    storeInitData,
    eraseHistory,
    eraseArchives,
    checkHistoryArchivesStatus,
    addGhostAgent,
    removeGhostAgent,
    registerScrollPositionGetter,
    clearScrollRestorePending,
    recordSessionReset
  }
})
