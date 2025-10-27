import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { useProjectStore } from './project'
import { useMessageStore } from './message'

/**
 * WebSocket Store - Manages WebSocket connections and message routing
 * Centralizes ALL WebSocket logic (previously scattered across 2000+ lines)
 */
export const useWebSocketStore = defineStore('websocket', () => {
  // ========== STATE ==========

  // UI WebSocket (global state updates)
  const uiSocket = ref(null)
  const uiConnected = ref(false)
  const uiRetryCount = ref(0)
  const maxUIRetries = 10

  // Session WebSocket (message streaming for current session)
  const sessionSocket = ref(null)
  const sessionConnected = ref(false)
  const sessionRetryCount = ref(0)
  const maxSessionRetries = 5
  const currentSessionId = ref(null)
  let sessionReconnectTimer = null // Track reconnect timer to cancel it

  // Connection generation tracking (prevents stale connections)
  const connectionGeneration = ref(0)
  const currentConnectionGeneration = ref(null)

  // Reconnection tracking (detect if this is initial connection or reconnection)
  const sessionHadInitialConnection = ref(new Map()) // sessionId -> boolean

  // Legion WebSocket (for timeline/spy/horde views)
  const legionSocket = ref(null)
  const legionConnected = ref(false)
  const legionRetryCount = ref(0)
  const maxLegionRetries = 5
  const currentLegionId = ref(null)

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

  /**
   * Get WebSocket URL (handles both dev and production)
   * In dev, Vite proxies WebSocket connections correctly
   */
  function getWebSocketUrl(path) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}${path}`
  }

  // ========== ACTIONS ==========

  /**
   * Connect to UI WebSocket (global state updates)
   */
  function connectUI() {
    if (uiSocket.value) return

    const wsUrl = getWebSocketUrl('/ws/ui')
    console.log(`Connecting to UI WebSocket: ${wsUrl}`)

    uiSocket.value = new WebSocket(wsUrl)

    uiSocket.value.onopen = () => {
      uiConnected.value = true
      uiRetryCount.value = 0
      console.log('UI WebSocket connected')
    }

    uiSocket.value.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleUIMessage(data)
    }

    uiSocket.value.onclose = () => {
      uiConnected.value = false
      uiSocket.value = null
      console.log('UI WebSocket closed')

      // Retry connection
      if (uiRetryCount.value < maxUIRetries) {
        uiRetryCount.value++
        const delay = Math.min(2000 * uiRetryCount.value, 30000)
        console.log(`Reconnecting UI WebSocket in ${delay}ms (attempt ${uiRetryCount.value})`)
        setTimeout(connectUI, delay)
      }
    }

    uiSocket.value.onerror = (error) => {
      console.error('UI WebSocket error:', error)
    }
  }

  /**
   * Disconnect UI WebSocket
   */
  function disconnectUI() {
    if (uiSocket.value) {
      uiSocket.value.close()
      uiSocket.value = null
      uiConnected.value = false
    }
  }

  /**
   * Connect to Session WebSocket (message streaming)
   * Now with generation tracking to prevent stale connections
   */
  async function connectSession(sessionId) {
    // Increment generation to invalidate any pending operations
    connectionGeneration.value++
    const generation = connectionGeneration.value

    console.log(`[Gen ${generation}] Starting connection to session ${sessionId}`)

    // Cancel any pending reconnect timer
    if (sessionReconnectTimer) {
      clearTimeout(sessionReconnectTimer)
      sessionReconnectTimer = null
    }

    // CRITICAL: Await disconnect to prevent race conditions
    await disconnectSession()

    // Double-check we're still on the same generation after await
    if (connectionGeneration.value !== generation) {
      console.log(`[Gen ${generation}] Connection cancelled (current generation: ${connectionGeneration.value})`)
      return
    }

    currentSessionId.value = sessionId
    currentConnectionGeneration.value = generation

    const wsUrl = getWebSocketUrl(`/ws/session/${sessionId}`)
    console.log(`[Gen ${generation}] Connecting to Session WebSocket: ${wsUrl}`)

    sessionSocket.value = new WebSocket(wsUrl)

    sessionSocket.value.onopen = async () => {
      // Validate generation before processing
      if (currentConnectionGeneration.value !== generation) {
        console.log(`[Gen ${generation}] Ignoring onopen (stale connection, current: ${currentConnectionGeneration.value})`)
        sessionSocket.value.close()
        return
      }

      sessionConnected.value = true
      sessionRetryCount.value = 0
      console.log(`[Gen ${generation}] Session WebSocket connected for ${sessionId}`)

      // Check if this is a reconnection (not initial connection)
      const isReconnection = sessionHadInitialConnection.value.get(sessionId)
      sessionHadInitialConnection.value.set(sessionId, true)

      // Sync missed messages on reconnection
      if (isReconnection) {
        console.log(`[Gen ${generation}] Reconnection detected, syncing missed messages...`)
        try {
          const messageStore = useMessageStore()
          const result = await messageStore.syncMessages(sessionId)

          if (result.syncedCount > 0) {
            console.log(`[Gen ${generation}] ✅ Synced ${result.syncedCount} missed messages`)
          } else if (result.error) {
            console.warn(`[Gen ${generation}] ⚠️ Message sync failed: ${result.error}`)
          } else {
            console.log(`[Gen ${generation}] No missed messages to sync`)
          }

          if (result.hasMore) {
            console.warn(`[Gen ${generation}] ⚠️ More than 1000 missed messages, showing recent 1000 only`)
          }
        } catch (error) {
          console.error(`[Gen ${generation}] Error during message sync:`, error)
          // Don't fail the connection if sync fails
        }
      } else {
        console.log(`[Gen ${generation}] Initial connection, no sync needed`)
      }
    }

    sessionSocket.value.onmessage = (event) => {
      // Validate generation before processing messages
      if (currentConnectionGeneration.value !== generation) {
        console.log(`[Gen ${generation}] Ignoring message (stale connection, current: ${currentConnectionGeneration.value})`)
        return
      }

      const data = JSON.parse(event.data)
      handleSessionMessage(data, sessionId)
    }

    sessionSocket.value.onclose = () => {
      sessionConnected.value = false
      sessionSocket.value = null
      console.log(`[Gen ${generation}] Session WebSocket closed for ${sessionId}`)

      // Only reconnect if:
      // 1. Still viewing this session
      // 2. Generation still matches (not superseded)
      // 3. Haven't exceeded retry limit
      const sessionStore = useSessionStore()
      if (sessionStore.currentSessionId === sessionId &&
          currentConnectionGeneration.value === generation &&
          sessionRetryCount.value < maxSessionRetries) {
        sessionRetryCount.value++
        const delay = Math.min(2000 * sessionRetryCount.value, 30000)
        console.log(`[Gen ${generation}] Reconnecting Session WebSocket in ${delay}ms (attempt ${sessionRetryCount.value})`)

        sessionReconnectTimer = setTimeout(() => {
          // Re-validate generation inside setTimeout (TOCTOU fix)
          if (currentConnectionGeneration.value === generation &&
              sessionStore.currentSessionId === sessionId) {
            sessionReconnectTimer = null
            connectSession(sessionId)
          } else {
            console.log(`[Gen ${generation}] Skipping reconnect (generation or session changed)`)
          }
        }, delay)
      }
    }

    sessionSocket.value.onerror = (error) => {
      console.error(`[Gen ${generation}] Session WebSocket error:`, error)
    }
  }

  /**
   * Disconnect Session WebSocket
   * Returns a promise that resolves when the socket is fully closed
   * Now with timeout fallback to prevent hanging
   */
  function disconnectSession() {
    return new Promise((resolve) => {
      // Cancel any pending reconnect timer
      if (sessionReconnectTimer) {
        clearTimeout(sessionReconnectTimer)
        sessionReconnectTimer = null
        console.log('Cancelled pending session reconnect timer')
      }

      if (sessionSocket.value) {
        // Capture sessionId before cleanup (fixes ReferenceError)
        const sessionId = currentSessionId.value

        const cleanup = () => {
          sessionSocket.value = null
          sessionConnected.value = false
          currentSessionId.value = null
          currentConnectionGeneration.value = null  // Clear generation
          sessionRetryCount.value = 0  // Reset retry count on explicit disconnect
          if (sessionId) {
            sessionHadInitialConnection.value.delete(sessionId)  // Clear reconnection flag
          }
          resolve()
        }

        // If already closed, cleanup immediately
        if (sessionSocket.value.readyState === WebSocket.CLOSED) {
          cleanup()
        } else {
          // Wait for close event to fire, with timeout fallback
          const timeoutId = setTimeout(() => {
            console.warn('Session WebSocket close timeout - forcing cleanup')
            cleanup()
          }, 2000)  // 2 second timeout

          sessionSocket.value.addEventListener('close', () => {
            clearTimeout(timeoutId)
            cleanup()
          }, { once: true })

          sessionSocket.value.close()
        }
      } else {
        // No socket to disconnect - still clear state
        currentSessionId.value = null
        currentConnectionGeneration.value = null
        resolve()
      }
    })
  }

  /**
   * Send message to session
   */
  function sendMessage(content) {
    if (sessionSocket.value && sessionConnected.value) {
      // Send to backend - backend will broadcast the user message back via WebSocket
      // This ensures all messages come from a single source (backend) to avoid duplicates
      sessionSocket.value.send(JSON.stringify({
        type: 'send_message',
        content: content
      }))
      console.log('Sent message to session')
    } else {
      console.error('Cannot send message: Session WebSocket not connected')
    }
  }

  /**
   * Send permission response
   */
  function sendPermissionResponse(requestId, decision, applySuggestions = false, clarification = null) {
    if (sessionSocket.value && sessionConnected.value) {
      const payload = {
        type: 'permission_response',
        request_id: requestId,
        decision: decision,
        apply_suggestions: applySuggestions,  // Always include boolean flag to match vanilla JS behavior
        timestamp: new Date().toISOString()
      }

      // Add clarification message if provided (backend expects 'clarification_message' field)
      if (clarification) {
        payload.clarification_message = clarification
      }

      sessionSocket.value.send(JSON.stringify(payload))
      console.log(`Sent permission ${decision} for request ${requestId}`, { applySuggestions, clarification })
    }
  }

  /**
   * Interrupt session processing
   */
  function interruptSession() {
    if (sessionSocket.value && sessionConnected.value) {
      sessionSocket.value.send(JSON.stringify({
        type: 'interrupt_session'
      }))
      console.log('Sent interrupt to session')
    }
  }

  /**
   * Handle UI WebSocket messages (global state updates)
   */
  function handleUIMessage(payload) {
    const sessionStore = useSessionStore()
    const projectStore = useProjectStore()

    switch (payload.type) {
      case 'sessions_list':
        // Update all sessions
        if (payload.sessions && Array.isArray(payload.sessions)) {
          payload.sessions.forEach(session => {
            sessionStore.updateSession(session.session_id, session)
          })
        }
        break

      case 'state_change':
        // Update specific session state
        // Backend sends: {type: "state_change", data: {session_id: "...", session: {...}, timestamp: "..."}}
        if (payload.data && payload.data.session_id && payload.data.session) {
          sessionStore.updateSession(payload.data.session_id, payload.data.session)
        }
        break

      case 'project_updated':
        // Update project (payload.data contains {project: {...}})
        if (payload.data && payload.data.project) {
          const project = payload.data.project
          projectStore.updateProjectLocal(project.project_id, project)
        }
        break

      case 'project_deleted':
        // Remove project (payload.data contains {project_id: "..."})
        if (payload.data && payload.data.project_id) {
          projectStore.projects.delete(payload.data.project_id)
        }
        break

      case 'connection_established':
        console.log('UI WebSocket: Connection established')
        break

      case 'ping':
        // Respond to keepalive ping with pong
        if (uiSocket.value?.readyState === WebSocket.OPEN) {
          uiSocket.value.send(JSON.stringify({
            type: 'pong',
            timestamp: new Date().toISOString()
          }))
        }
        break

      default:
        console.warn('Unknown UI WebSocket message type:', payload.type)
    }
  }

  /**
   * Handle Session WebSocket messages (message streaming)
   * Now with generation validation to prevent processing stale messages
   */
  function handleSessionMessage(payload, sessionId) {
    // Validate this message is for the currently selected session
    // This is an extra safety check on top of onmessage validation
    const sessionStore = useSessionStore()
    if (sessionStore.currentSessionId !== sessionId) {
      console.warn(`Ignoring message for session ${sessionId} (current: ${sessionStore.currentSessionId})`)
      return
    }

    const messageStore = useMessageStore()

    switch (payload.type) {
      case 'message':
        // Handle different message types
        // Backend sends WebSocket payload: {type: "message", session_id: "...", data: {actual message}, timestamp: "..."}
        // Extract the actual message from payload.data
        const message = payload.data

        // Safety check
        if (!message || !message.type) {
          console.warn('Received message event with invalid data:', payload)
          break
        }

        // Handle permission_request messages
        if (message.type === 'permission_request') {
          messageStore.handlePermissionRequest(sessionId, message)
          // Don't add to message history - it's handled by tool call
          break
        }

        // Handle permission_response messages
        if (message.type === 'permission_response') {
          messageStore.handlePermissionResponse(sessionId, message)
          // Don't add to message history - it's handled by tool call
          break
        }

        // Process tool-related messages
        if (message.metadata?.has_tool_uses) {
          // Extract and create tool call cards from assistant messages
          message.metadata.tool_uses.forEach(toolUse => {
            messageStore.handleToolUse(sessionId, toolUse)
          })
        }

        if (message.metadata?.has_tool_results) {
          // Extract and update tool call cards from user messages
          message.metadata.tool_results.forEach(toolResult => {
            messageStore.handleToolResult(sessionId, toolResult)
          })
        }

        // Capture init data for session info modal
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }

        // Add message to history
        messageStore.addMessage(sessionId, message)
        break

      case 'permission_request':
        // Handle permission request
        messageStore.handlePermissionRequest(sessionId, payload)
        break

      case 'permission_response':
        // Handle permission response (from backend)
        messageStore.handlePermissionResponse(sessionId, payload)
        break

      case 'tool_result':
        // Handle tool completion
        messageStore.handleToolResult(sessionId, payload)
        break

      case 'state_change':
        // Session state changed
        sessionStore.updateSession(sessionId, payload.updates)
        break

      case 'connection_established':
        console.log(`Session WebSocket: Connection established for ${sessionId}`)
        break

      case 'ping':
        // Respond to keepalive ping with pong
        if (sessionSocket.value?.readyState === WebSocket.OPEN) {
          sessionSocket.value.send(JSON.stringify({
            type: 'pong',
            timestamp: new Date().toISOString()
          }))
        }
        break

      default:
        console.warn('Unknown Session WebSocket message type:', data.type)
    }
  }

  /**
   * Connect to Legion WebSocket (for timeline/spy/horde views)
   */
  function connectLegion(legionId) {
    disconnectLegion()

    currentLegionId.value = legionId

    const wsUrl = getWebSocketUrl(`/ws/legion/${legionId}`)
    console.log(`Connecting to Legion WebSocket: ${wsUrl}`)

    legionSocket.value = new WebSocket(wsUrl)

    legionSocket.value.onopen = () => {
      legionConnected.value = true
      legionRetryCount.value = 0
      console.log(`Legion WebSocket connected for ${legionId}`)
    }

    legionSocket.value.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleLegionMessage(data, legionId)
    }

    legionSocket.value.onclose = () => {
      legionConnected.value = false
      legionSocket.value = null
      console.log(`Legion WebSocket closed for ${legionId}`)

      // Retry if still viewing this legion
      if (currentLegionId.value === legionId && legionRetryCount.value < maxLegionRetries) {
        legionRetryCount.value++
        const delay = Math.min(2000 * legionRetryCount.value, 30000)
        setTimeout(() => connectLegion(legionId), delay)
      }
    }

    legionSocket.value.onerror = (error) => {
      console.error('Legion WebSocket error:', error)
    }
  }

  /**
   * Disconnect Legion WebSocket
   */
  function disconnectLegion() {
    if (legionSocket.value) {
      legionSocket.value.close()
      legionSocket.value = null
      legionConnected.value = false
      currentLegionId.value = null
    }
  }

  /**
   * Handle Legion WebSocket messages
   */
  function handleLegionMessage(payload, legionId) {
    // Import legion store dynamically to avoid circular dependency
    import('./legion').then(({ useLegionStore }) => {
      const legionStore = useLegionStore()

      switch (payload.type) {
        case 'comm':
          // New communication received
          // Backend sends: { type: "comm", comm: {...}, timestamp: ... }
          if (payload.comm) {
            legionStore.addComm(legionId, payload.comm)
            console.log('Legion WebSocket: Received new comm', payload.comm)
          }
          break

        case 'minion_created':
          // New minion added to legion - reload all sessions to get updated parent child_minion_ids
          if (payload.data) {
            console.log('Legion WebSocket: Minion created, reloading sessions', payload.data)
            // Import session store to reload session data
            import('./session').then(({ useSessionStore }) => {
              const sessionStore = useSessionStore()
              // Reload all sessions for this project to get updated child_minion_ids
              sessionStore.loadSessions()
            })
          }
          break

        case 'minion_updated':
          // Minion state changed - reload all sessions
          if (payload.data) {
            console.log('Legion WebSocket: Minion updated, reloading sessions', payload.data)
            import('./session').then(({ useSessionStore }) => {
              const sessionStore = useSessionStore()
              sessionStore.loadSessions()
            })
          }
          break

        case 'connection_established':
          console.log(`Legion WebSocket: Connection established for ${legionId}`)
          break

        case 'ping':
          // Respond to keepalive ping with pong
          if (legionSocket.value?.readyState === WebSocket.OPEN) {
            legionSocket.value.send(JSON.stringify({
              type: 'pong',
              timestamp: new Date().toISOString()
            }))
          }
          break

        default:
          console.warn('Unknown Legion WebSocket message type:', payload.type)
      }
    })
  }

  // ========== RETURN ==========
  return {
    // State
    uiConnected,
    sessionConnected,
    legionConnected,
    overallStatus,

    // Actions
    connectUI,
    disconnectUI,
    connectSession,
    disconnectSession,
    sendMessage,
    sendPermissionResponse,
    interruptSession,
    connectLegion,
    disconnectLegion
  }
})
