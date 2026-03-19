import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { useProjectStore } from './project'
import { useMessageStore } from './message'
import { useResourceStore } from './resource'
import { useQueueStore } from './queue'
import { notify } from '@/composables/useNotifications'
import { getAuthToken, api } from '@/utils/api'

export const useWebSocketStore = defineStore('websocket', () => {
  // ========== STATE ==========
  const uiConnected = ref(false)
  const uiRetryCount = ref(0)
  const sessionConnected = ref(false)
  const sessionRetryCount = ref(0)
  const currentSessionId = ref(null)
  const currentLegionId = ref(null)  // stub - always null
  const legionConnected = ref(false)  // stub - always false
  const legionRetryCount = ref(0)    // stub

  // Poll cursors
  let uiCursor = 0
  const sessionCursors = {}  // Per-session cursor cache to avoid replaying history on switch

  // AbortControllers for long-poll requests
  let uiAbortController = null
  let sessionAbortController = null

  // Loop control flags
  let uiPollGeneration = 0

  // ========== COMPUTED ==========
  const overallStatus = computed(() => {
    if (uiConnected.value && (sessionConnected.value || !currentSessionId.value)) {
      return 'connected'
    }
    if (uiConnected.value) {
      return 'partial'
    }
    return 'disconnected'
  })

  // ========== HELPERS ==========
  function getPollUrl(path, cursor, timeout = 30) {
    const token = getAuthToken()
    const base = `${path}?since=${cursor}&timeout=${timeout}`
    return token ? `${base}&token=${encodeURIComponent(token)}` : base
  }

  // ========== UI POLL LOOP ==========
  async function startUIPolling() {
    if (uiConnected.value) return
    uiConnected.value = true
    uiRetryCount.value = 0
    const myGeneration = ++uiPollGeneration

    while (uiConnected.value) {
      try {
        uiAbortController = new AbortController()
        const url = getPollUrl('/api/poll/ui', uiCursor)
        uiConnected.value = true
        const response = await fetch(url, { signal: uiAbortController.signal })

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        uiRetryCount.value = 0

        if (data.events && data.events.length > 0) {
          for (const event of data.events) {
            handleUIMessage(event)
          }
        }
        uiCursor = data.next_cursor

      } catch (err) {
        if (err.name === 'AbortError') {
          // Visibility resume or stop — re-enter immediately if still polling
          continue
        }
        uiConnected.value = false
        uiRetryCount.value++
        const delay = Math.min(2000 * uiRetryCount.value, 30000)
        await new Promise(resolve => setTimeout(resolve, delay))
        if (uiPollGeneration === myGeneration) {
          uiConnected.value = true
        }
      }
    }
  }

  function stopUIPolling() {
    uiPollGeneration++
    uiConnected.value = false
    uiAbortController?.abort()
    uiAbortController = null
  }

  // Compat aliases
  function connectUI() { startUIPolling() }
  function disconnectUI() { stopUIPolling() }

  // ========== SESSION POLL LOOP ==========
  async function connectSession(sessionId) {
    // Stop any existing session poll
    await disconnectSession()

    currentSessionId.value = sessionId
    sessionConnected.value = true
    sessionRetryCount.value = 0

    // On first connection to this session, start from cursor 0.
    // loadMessages() covers history via REST; the message store deduplicates by ID.
    // On reconnect (cached cursor exists), resume from where we left off.
    if (sessionCursors[sessionId] === undefined) {
      sessionCursors[sessionId] = 0
    }

    while (sessionConnected.value && currentSessionId.value === sessionId) {
      try {
        sessionAbortController = new AbortController()
        const url = getPollUrl(`/api/poll/session/${sessionId}`, sessionCursors[sessionId])
        const response = await fetch(url, { signal: sessionAbortController.signal })

        if (response.status === 404) {
          // Session not found - stop polling
          sessionConnected.value = false
          break
        }

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        sessionRetryCount.value = 0

        if (data.events && data.events.length > 0) {
          for (const event of data.events) {
            handleSessionMessage(event, sessionId)
          }
        }
        sessionCursors[sessionId] = data.next_cursor

      } catch (err) {
        if (err.name === 'AbortError') {
          continue
        }
        sessionConnected.value = false
        sessionRetryCount.value++
        const delay = Math.min(2000 * sessionRetryCount.value, 30000)
        await new Promise(resolve => setTimeout(resolve, delay))
        if (currentSessionId.value === sessionId) {
          sessionConnected.value = true
        }
      }
    }
  }

  function disconnectSession() {
    return new Promise(resolve => {
      sessionConnected.value = false
      currentSessionId.value = null
      sessionAbortController?.abort()
      sessionAbortController = null
      resolve()
    })
  }

  // ========== PAGE VISIBILITY ==========
  function setupVisibilityHandler() {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        uiAbortController?.abort()
        sessionAbortController?.abort()
      }
    })
  }

  // ========== OUTBOUND REST ACTIONS ==========
  async function sendMessage(content) {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    if (!sid) {
      console.error('Cannot send message: no current session')
      return
    }
    try {
      await api.post(`/api/sessions/${sid}/messages`, { message: content })
      setTimeout(() => {
        import('./mcp').then(({ useMcpStore }) => {
          useMcpStore().fetchMcpStatus(sid)
        })
      }, 2000)
    } catch (err) {
      console.error('Failed to send message:', err)
    }
  }

  async function sendPermissionResponse(requestId, decision, applySuggestions = false, clarification = null, selectedSuggestions = null) {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    if (!sid) return
    try {
      const payload = {
        decision,
        apply_suggestions: applySuggestions,
      }
      if (clarification) payload.clarification_message = clarification
      if (selectedSuggestions) payload.selected_suggestions = selectedSuggestions
      await api.post(`/api/sessions/${sid}/permission/${requestId}`, payload)
    } catch (err) {
      console.error('Failed to send permission response:', err)
    }
  }

  async function sendPermissionResponseWithInput(requestId, decision, updatedInput) {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    if (!sid) return
    try {
      await api.post(`/api/sessions/${sid}/permission/${requestId}`, {
        decision,
        updated_input: updatedInput,
      })
    } catch (err) {
      console.error('Failed to send permission response with input:', err)
    }
  }

  async function interruptSession() {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    if (!sid) return
    try {
      await api.post(`/api/sessions/${sid}/interrupt`, {})
    } catch (err) {
      console.error('Failed to interrupt session:', err)
    }
  }

  // No-op stubs for legion
  function connectLegion() {}
  function disconnectLegion() {}

  // ========== MESSAGE HANDLERS ==========
  function handleUIMessage(payload) {
    const sessionStore = useSessionStore()
    const projectStore = useProjectStore()

    switch (payload.type) {
      case 'sessions_list':
        if (payload.sessions && Array.isArray(payload.sessions)) {
          payload.sessions.forEach(session => {
            sessionStore.updateSession(session.session_id, session)
          })
        }
        break

      case 'state_change':
        if (payload.data && payload.data.session_id && payload.data.session) {
          const priorSession = sessionStore.sessions.get(payload.data.session_id)
          const wasProcessing = priorSession?.is_processing

          sessionStore.updateSession(payload.data.session_id, payload.data.session)

          const changedSessionId = payload.data.session_id
          const newState = payload.data.session.state

          if (newState === 'error') {
            console.log(`[UI state_change] Session ${changedSessionId} entered error state, reloading messages`)
            const messageStore = useMessageStore()
            messageStore.loadMessages(changedSessionId)
          }

          if (newState === 'active') {
            import('./mcp').then(({ useMcpStore }) => {
              const mcpStore = useMcpStore()
              mcpStore.fetchMcpStatus(changedSessionId)
            })
          }

          if (newState === 'error') {
            notify('session_error', { sessionName: payload.data.session.name || 'Session' })
          }
          if (wasProcessing && !payload.data.session.is_processing) {
            notify('task_complete', { sessionName: payload.data.session.name || 'Session' })
          }
          if (newState === 'paused' && priorSession?.state !== 'paused') {
            notify('permission_prompt', { sessionName: payload.data.session.name || 'Session' })
          }
        }
        break

      case 'session_reset': {
        const resetSessionId = payload.data?.session_id
        if (resetSessionId) {
          const messageStore = useMessageStore()
          messageStore.clearMessages(resetSessionId)
          const resourceStore = useResourceStore()
          resourceStore.clearResources(resetSessionId)
          const sessionStore2 = useSessionStore()
          sessionStore2.recordSessionReset(resetSessionId)
        }
        break
      }

      case 'project_updated':
        if (payload.data && payload.data.project) {
          const project = payload.data.project
          projectStore.updateProjectLocal(project.project_id, project)
          sessionStore.fetchSessions()
        }
        break

      case 'project_deleted':
        if (payload.data && payload.data.project_id) {
          projectStore.projects.delete(payload.data.project_id)
        }
        break

      case 'notification':
        if (payload.data?.event_type === 'minion_comm') {
          notify('minion_comm', {
            commType: payload.data.comm_type,
            fromMinion: payload.data.from_minion_name || 'Minion'
          })
        }
        break

      case 'mcp_oauth_complete': {
        const serverId = payload.server_id
        if (serverId) {
          import('./mcpConfig').then(({ useMcpConfigStore }) => {
            useMcpConfigStore().fetchOAuthStatus(serverId)
          })
        }
        break
      }

      case 'schedule_updated':
        import('./schedule').then(({ useScheduleStore }) => {
          const scheduleStore = useScheduleStore()
          scheduleStore.handleScheduleEvent(payload.legion_id || payload.data?.legion_id, payload)
        })
        break

      case 'schedule_execution':
        import('./schedule').then(({ useScheduleStore }) => {
          const scheduleStore = useScheduleStore()
          scheduleStore.handleScheduleExecution(payload.legion_id || payload.data?.legion_id, payload)
        })
        break

      default:
        console.warn('Unknown UI poll message type:', payload.type)
    }
  }

  function handleSessionMessage(payload, sessionId) {
    const sessionStore = useSessionStore()
    if (sessionStore.currentSessionId !== sessionId) {
      return
    }

    const messageStore = useMessageStore()

    switch (payload.type) {
      case 'message': {
        const message = payload.data
        if (!message || !message.type) {
          console.warn('Received message event with invalid data:', payload)
          break
        }
        if (message.type === 'tool_call') {
          messageStore.handleToolCall(sessionId, message)
          break
        }
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }
        messageStore.addMessage(sessionId, message)
        break
      }

      case 'tool_call':
        messageStore.handleToolCall(sessionId, payload.data || payload)
        break

      case 'resource_registered':
        if (payload.resource) {
          const resourceStore = useResourceStore()
          resourceStore.addResource(sessionId, payload.resource)
        }
        break

      case 'resource_removed':
        if (payload.resource_id) {
          const resourceStore = useResourceStore()
          resourceStore.handleResourceRemoved(sessionId, payload.resource_id)
        }
        break

      case 'image_registered':
        if (payload.image) {
          const resourceStore = useResourceStore()
          resourceStore.addResource(sessionId, {
            ...payload.image,
            resource_id: payload.image.image_id
          })
        }
        break

      case 'queue_update': {
        const queueStore = useQueueStore()
        queueStore.handleQueueUpdate(sessionId, payload)
        break
      }

      default:
        console.warn('Unknown session poll message type:', payload.type)
    }
  }

  // ========== RETURN ==========
  return {
    uiConnected,
    uiRetryCount,
    sessionConnected,
    sessionRetryCount,
    legionConnected,
    legionRetryCount,
    currentLegionId,
    overallStatus,

    connectUI,
    disconnectUI,
    startUIPolling,
    stopUIPolling,
    setupVisibilityHandler,
    connectSession,
    disconnectSession,
    sendMessage,
    sendPermissionResponse,
    sendPermissionResponseWithInput,
    interruptSession,
    connectLegion,
    disconnectLegion,
  }
})
