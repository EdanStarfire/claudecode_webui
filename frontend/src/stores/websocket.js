import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { useProjectStore } from './project'
import { useMessageStore } from './message'
import { useResourceStore } from './resource'
import { useQueueStore } from './queue'

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

  // Legion WebSocket (for timeline/hierarchy views)
  const legionSocket = ref(null)
  const legionConnected = ref(false)
  const legionRetryCount = ref(0)
  const maxLegionRetries = 5
  const currentLegionId = ref(null)

  // Heartbeat monitoring configuration
  const PING_TIMEOUT_MS = 10000  // 10 seconds without ping = disconnected (allows 3+ ping attempts)
  const HEARTBEAT_CHECK_INTERVAL_MS = 1000  // Check every second

  // Heartbeat last received timestamps
  const uiLastPingTime = ref(Date.now())
  const sessionLastPingTime = ref(Date.now())
  const legionLastPingTime = ref(Date.now())

  // Heartbeat check timers
  let uiHeartbeatTimer = null
  let sessionHeartbeatTimer = null
  let legionHeartbeatTimer = null

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

  /**
   * Start heartbeat timeout monitoring for a connection.
   * Marks connection as dead if no ping received within PING_TIMEOUT_MS.
   */
  function startHeartbeatMonitor(type) {
    const config = {
      ui: {
        timer: () => uiHeartbeatTimer,
        setTimer: (val) => { uiHeartbeatTimer = val },
        lastPing: uiLastPingTime,
        connected: uiConnected,
        name: 'UI'
      },
      session: {
        timer: () => sessionHeartbeatTimer,
        setTimer: (val) => { sessionHeartbeatTimer = val },
        lastPing: sessionLastPingTime,
        connected: sessionConnected,
        name: 'Session'
      },
      legion: {
        timer: () => legionHeartbeatTimer,
        setTimer: (val) => { legionHeartbeatTimer = val },
        lastPing: legionLastPingTime,
        connected: legionConnected,
        name: 'Legion'
      }
    }[type]

    // Clear any existing timer
    if (config.timer()) {
      clearInterval(config.timer())
    }

    // Start heartbeat monitoring
    config.setTimer(setInterval(() => {
      if (!config.connected.value) return // Already disconnected

      const timeSincePing = Date.now() - config.lastPing.value
      if (timeSincePing > PING_TIMEOUT_MS) {
        console.warn(`⚠️ ${config.name} WebSocket heartbeat timeout (${timeSincePing}ms since last ping) - forcing close to trigger reconnection`)
        config.connected.value = false

        // CRITICAL: Manually close the socket to trigger onclose handler and reconnection logic
        // The socket is still technically "open" from browser's perspective, but network is dead
        const socketMap = {
          ui: uiSocket,
          session: sessionSocket,
          legion: legionSocket
        }
        const socket = socketMap[type]
        if (socket.value && socket.value.readyState === WebSocket.OPEN) {
          socket.value.close()
        }
      }
    }, HEARTBEAT_CHECK_INTERVAL_MS))

    console.log(`✅ Started heartbeat monitor for ${config.name} WebSocket`)
  }

  /**
   * Stop heartbeat monitoring for a connection
   */
  function stopHeartbeatMonitor(type) {
    const timers = {
      ui: { get: () => uiHeartbeatTimer, set: (val) => { uiHeartbeatTimer = val } },
      session: { get: () => sessionHeartbeatTimer, set: (val) => { sessionHeartbeatTimer = val } },
      legion: { get: () => legionHeartbeatTimer, set: (val) => { legionHeartbeatTimer = val } }
    }[type]

    if (timers.get()) {
      clearInterval(timers.get())
      timers.set(null)
      console.log(`🛑 Stopped heartbeat monitor for ${type} WebSocket`)
    }
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
      uiLastPingTime.value = Date.now()  // Reset ping timestamp
      startHeartbeatMonitor('ui')  // Start monitoring
      console.log('UI WebSocket connected')
    }

    uiSocket.value.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleUIMessage(data)
    }

    uiSocket.value.onclose = () => {
      stopHeartbeatMonitor('ui')  // Stop monitoring
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
      sessionLastPingTime.value = Date.now()  // Reset ping timestamp
      startHeartbeatMonitor('session')  // Start monitoring
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
      stopHeartbeatMonitor('session')  // Stop monitoring
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
   * @param {string} requestId - Permission request ID
   * @param {string} decision - 'allow' or 'deny'
   * @param {boolean} applySuggestions - Whether to apply suggestions
   * @param {string|null} clarification - Optional guidance message
   * @param {Array|null} selectedSuggestions - Optional filtered suggestions array (only checked items)
   */
  function sendPermissionResponse(requestId, decision, applySuggestions = false, clarification = null, selectedSuggestions = null) {
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

      // Add selected suggestions array if provided (for granular permission selection)
      if (selectedSuggestions) {
        payload.selected_suggestions = selectedSuggestions
      }

      sessionSocket.value.send(JSON.stringify(payload))
      console.log(`Sent permission ${decision} for request ${requestId}`, { applySuggestions, clarification, selectedSuggestions })
    }
  }

  /**
   * Send permission response with updated_input (for AskUserQuestion)
   * @param {string} requestId - The permission request ID
   * @param {string} decision - 'allow' or 'deny'
   * @param {object} updatedInput - The updated input containing questions and answers
   */
  function sendPermissionResponseWithInput(requestId, decision, updatedInput) {
    if (sessionSocket.value && sessionConnected.value) {
      const payload = {
        type: 'permission_response',
        request_id: requestId,
        decision: decision,
        updated_input: updatedInput,
        timestamp: new Date().toISOString()
      }

      sessionSocket.value.send(JSON.stringify(payload))
      console.log(`Sent permission ${decision} with updated_input for request ${requestId}`, updatedInput)
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
          // Reload sessions when project is updated (minion spawn/dispose changes session list)
          sessionStore.fetchSessions()
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
        uiLastPingTime.value = Date.now()  // Update heartbeat timestamp
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

        // Issue #324/#491: Handle unified tool_call messages (single path for all tool lifecycle)
        if (message.type === 'tool_call') {
          messageStore.handleToolCall(sessionId, message)
          // Don't add to message history - tool_call is its own display entity
          break
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

      case 'state_change':
        // Session state changed
        sessionStore.updateSession(sessionId, payload.updates)
        break

      case 'connection_established':
        console.log(`Session WebSocket: Connection established for ${sessionId}`)
        break

      case 'ping':
        sessionLastPingTime.value = Date.now()  // Update heartbeat timestamp
        // Respond to keepalive ping with pong
        if (sessionSocket.value?.readyState === WebSocket.OPEN) {
          sessionSocket.value.send(JSON.stringify({
            type: 'pong',
            timestamp: new Date().toISOString()
          }))
        }
        break

      // Issue #404: Handle resource_registered from MCP tool
      case 'resource_registered':
        if (payload.resource) {
          const resourceStore = useResourceStore()
          resourceStore.addResource(sessionId, payload.resource)
          console.log(`Resource registered for session ${sessionId}:`, payload.resource.resource_id)
        }
        break

      // Issue #423: Handle resource_removed (multi-client sync)
      case 'resource_removed':
        if (payload.resource_id) {
          const resourceStore = useResourceStore()
          resourceStore.handleResourceRemoved(sessionId, payload.resource_id)
          console.log(`Resource removed for session ${sessionId}:`, payload.resource_id)
        }
        break

      // Backward compatibility: Handle image_registered from legacy MCP tool
      case 'image_registered':
        if (payload.image) {
          const resourceStore = useResourceStore()
          // Convert image payload to resource format
          resourceStore.addResource(sessionId, {
            ...payload.image,
            resource_id: payload.image.image_id
          })
          console.log(`Image registered (legacy) for session ${sessionId}:`, payload.image.image_id)
        }
        break

      // Issue #500: Handle queue updates
      case 'queue_update': {
        const queueStore = useQueueStore()
        queueStore.handleQueueUpdate(sessionId, payload)
        break
      }

      default:
        console.warn('Unknown Session WebSocket message type:', payload.type)
    }
  }

  /**
   * Connect to Legion WebSocket (for timeline/hierarchy views)
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
      legionLastPingTime.value = Date.now()  // Reset ping timestamp
      startHeartbeatMonitor('legion')  // Start monitoring
      console.log(`Legion WebSocket connected for ${legionId}`)
    }

    legionSocket.value.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleLegionMessage(data, legionId)
    }

    legionSocket.value.onclose = () => {
      stopHeartbeatMonitor('legion')  // Stop monitoring
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
          legionLastPingTime.value = Date.now()  // Update heartbeat timestamp
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
    uiRetryCount,
    sessionConnected,
    sessionRetryCount,
    legionConnected,
    legionRetryCount,
    currentLegionId,
    overallStatus,

    // Actions
    connectUI,
    disconnectUI,
    connectSession,
    disconnectSession,
    sendMessage,
    sendPermissionResponse,
    sendPermissionResponseWithInput,
    interruptSession,
    connectLegion,
    disconnectLegion
  }
})
