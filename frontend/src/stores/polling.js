import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { useProjectStore } from './project'
import { useMessageStore } from './message'
import { useResourceStore } from './resource'
import { useQueueStore } from './queue'
import { useUIStore } from './ui'
import { useEditHistoryStore } from './editHistory'
import { notify } from '@/composables/useNotifications'
import { getAuthToken, api } from '@/utils/api'
import { pushDebugEvent } from '@/composables/useDebugBuffer'

export const usePollingStore = defineStore('polling', () => {
  // ========== STATE ==========
  const uiConnected = ref(false)
  const uiRetryCount = ref(0)
  const sessionConnected = ref(false)
  const sessionRetryCount = ref(0)
  const sessionStalled = ref(false)
  const currentSessionId = ref(null)
  const currentLegionId = ref(null)  // stub - always null
  const legionConnected = ref(false)  // stub - always false
  const legionRetryCount = ref(0)    // stub

  // Poll cursors
  let uiCursor = 0
  const sessionCursors = {}  // Per-session cursor cache to avoid replaying history on switch
  const sessionPollHeartbeatAt = {}  // Per-session last-successful-poll timestamp (Issue #1795)
  const sessionHealInFlight = {}  // Per-session stall-heal mutex (Issue #1954) — distinct from the HEAL_COOLDOWN_MS rate limiter below

  // AbortControllers for long-poll requests
  let uiAbortController = null
  let sessionAbortController = null

  // Loop control flags
  let uiPollGeneration = 0
  let sessionPollGeneration = 0

  // Session loop exit promise — used by disconnectSession() to await clean teardown
  let sessionLoopExitPromise = null

  // Stall detector state
  let stallDetectorInterval = null
  let lastHealedAt = 0
  // Issue #1795: coupling note — the heartbeat measures poll round-trip freshness
  // (not message content), so this must stay above the server's long-poll hold time
  // (src/routers/poll.py: effective_timeout = min(timeout, 30.0)) regardless of
  // is_processing, or a healthy-but-quiet connection will false-positive on every
  // poll cycle. Applies uniformly to active and idle sessions alike.
  const STALL_TIMEOUT_MS = 40000
  const HEAL_COOLDOWN_MS = 10000
  const STALL_CHECK_INTERVAL_MS = 5000

  // Page Visibility cleanup
  let visibilityUnsubscribe = null

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
  async function startUIPolling(initialCursor = 0) {
    if (uiConnected.value) return
    uiCursor = initialCursor
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
        pushDebugEvent('polling', 'poll-cycle', {
          stream: 'ui', cursorBefore: uiCursor, cursorAfter: data.next_cursor, generation: myGeneration
        })
        uiCursor = data.next_cursor

      } catch (err) {
        if (err.name === 'AbortError') {
          // Visibility resume or stop — re-enter immediately if still polling
          continue
        }
        console.warn(`[UI poll] Connection error (retry ${uiRetryCount.value + 1}):`, err.message || err)
        pushDebugEvent('polling', 'poll-error', {
          stream: 'ui', message: err.message || String(err), retryCount: uiRetryCount.value + 1, generation: myGeneration
        })
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
  async function _runSessionPollLoop(sessionId, myGeneration) {
    while (sessionPollGeneration === myGeneration
           && sessionConnected.value
           && currentSessionId.value === sessionId) {
      try {
        sessionAbortController = new AbortController()
        // Fix 4: normalize cursor to avoid ?since=undefined producing a 422
        const rawCursor = sessionCursors[sessionId]
        const cursor = (typeof rawCursor === 'number') ? rawCursor : 0
        const url = getPollUrl(`/api/poll/session/${sessionId}`, cursor)
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
        // Issue #1795: any successful response — even one carrying zero events after
        // the server's long-poll hold — proves this connection is alive.
        sessionPollHeartbeatAt[sessionId] = Date.now()
        // Issue #1960: clear immediately on success rather than waiting up to
        // STALL_CHECK_INTERVAL_MS for the next watchdog tick to notice recovery.
        // Guarded by generation + session identity (mirroring the retry branch's guard
        // below) since this is a single global flag, not keyed per session like
        // sessionPollHeartbeatAt — an in-flight response from a since-superseded
        // generation (e.g. a stall-heal reconnect racing its own old loop) must not
        // clear the flag for whatever session/generation is current now.
        if (sessionPollGeneration === myGeneration && currentSessionId.value === sessionId) {
          sessionStalled.value = false
        }

        if (data.events && data.events.length > 0) {
          for (const event of data.events) {
            handleSessionMessage(event, sessionId)
          }
        }
        pushDebugEvent('polling', 'poll-cycle', {
          stream: 'session', sessionId, cursorBefore: cursor, cursorAfter: data.next_cursor, generation: myGeneration
        })
        sessionCursors[sessionId] = data.next_cursor

      } catch (err) {
        if (err.name === 'AbortError') {
          continue
        }
        console.warn(`[Session poll] Connection error for session ${sessionId} (retry ${sessionRetryCount.value + 1}):`, err.message || err)
        pushDebugEvent('polling', 'poll-error', {
          stream: 'session', sessionId, message: err.message || String(err), retryCount: sessionRetryCount.value + 1, generation: myGeneration
        })
        sessionConnected.value = false
        sessionRetryCount.value++
        const delay = Math.min(2000 * sessionRetryCount.value, 30000)
        // Fix 2: coupling note — this sleep is 2s; disconnectSession timeout must stay >2s
        await new Promise(resolve => setTimeout(resolve, delay))
        // Fix 1: guard generation before re-enabling connected — superseded loop must not re-arm
        if (sessionPollGeneration === myGeneration && currentSessionId.value === sessionId) {
          sessionConnected.value = true
        }
      }
    }
  }

  async function connectSession(sessionId) {
    // Stop any existing session poll (Fix 2: truly awaits loop exit)
    await disconnectSession()

    const myGeneration = ++sessionPollGeneration  // Fix 1: generation guard
    currentSessionId.value = sessionId
    sessionConnected.value = true
    sessionRetryCount.value = 0
    // Issue #1960: prevent a previously-viewed session's stalled flag from leaking
    // onto the newly-selected session.
    sessionStalled.value = false

    // Issue #1000: Prefer cursor from loadMessages() REST response (aligned with
    // loaded history). Fall back to API bootstrap for first-time connections.
    const messageStore = useMessageStore()
    const restCursor = messageStore.loadedEventCursors.get(sessionId)
    if (restCursor !== undefined) {
      sessionCursors[sessionId] = restCursor
      messageStore.loadedEventCursors.delete(sessionId)
    } else if (sessionCursors[sessionId] === undefined) {
      try {
        const result = await api.get(`/api/poll/session/${sessionId}/cursor`)
        sessionCursors[sessionId] = result?.cursor ?? 0
      } catch {
        sessionCursors[sessionId] = 0
      }
    }

    // Issue #1795: seed the heartbeat before the loop starts so staleness is measurable
    // from t=0 — closes a latent gap where a hung first fetch left no baseline to compare.
    sessionPollHeartbeatAt[sessionId] = Date.now()

    // Capture the loop promise so disconnectSession() can await clean exit (Fix 2)
    sessionLoopExitPromise = _runSessionPollLoop(sessionId, myGeneration)

    // Start stall detector (Fix 5: idempotent, early-returns when sid is null)
    startStallDetector()
  }

  async function disconnectSession() {
    // Fix 1: bump generation first so any in-setTimeout loop exits on timer fire
    sessionPollGeneration++
    sessionConnected.value = false
    currentSessionId.value = null
    sessionAbortController?.abort()
    sessionAbortController = null
    stopStallDetector()
    // Fix 2: await loop exit with 3s budget (must exceed 2s catch-block sleep)
    if (sessionLoopExitPromise) {
      try {
        const TIMED_OUT = Symbol('timed-out')
        const result = await Promise.race([
          sessionLoopExitPromise.then(() => 'exited'),
          new Promise(resolve => setTimeout(() => resolve(TIMED_OUT), 3000))
        ])
        // Issue #1917 (Fix B2): a slow-to-exit old loop silently proceeding was previously
        // invisible — surface it so this timing edge case can be diagnosed from logs.
        if (result === TIMED_OUT) {
          console.warn('[disconnectSession] old poll loop did not exit within 3s budget — proceeding anyway')
        }
      } catch { /* ignore */ }
      sessionLoopExitPromise = null
    }
  }

  function resetSessionCursor(sessionId) {
    delete sessionCursors[sessionId]
    delete sessionPollHeartbeatAt[sessionId]
    delete sessionHealInFlight[sessionId]
    // Issue #1960: prevent a reset session's stalled flag from leaking onto whatever
    // session is current afterward.
    sessionStalled.value = false
  }

  // ========== STALL DETECTOR (Fix 5) ==========
  function startStallDetector() {
    if (stallDetectorInterval) return
    stallDetectorInterval = setInterval(checkSessionStall, STALL_CHECK_INTERVAL_MS)
  }

  // Issue #1960: closes a leak where stallDetectorInterval was created once and never
  // cleared — startStallDetector()'s `if (stallDetectorInterval) return` guard already
  // makes re-starting idempotent, so this only stops the leak without changing runtime
  // behavior for the normal case.
  function stopStallDetector() {
    if (stallDetectorInterval) {
      clearInterval(stallDetectorInterval)
      stallDetectorInterval = null
    }
  }

  async function checkSessionStall() {
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()
    const sid = currentSessionId.value
    // Issue #1960: default to false so every early-return below (inapplicable or
    // not-yet-measurable session) leaves the flag correctly cleared — the single
    // `stallMs >= STALL_TIMEOUT_MS` assignment further down is the only place it's
    // ever set true, so a future added early-return can't forget to reset it.
    sessionStalled.value = false
    if (!sid) return

    const session = sessionStore.sessions.get(sid)
    if (!session) return
    if (session.state === 'paused') return

    // Issue #1795: liveness is derived from the session-poll connection's own heartbeat,
    // not from is_processing (which is written exclusively by the separate UI-poll channel
    // and can flip false while this connection is silently dead). The single threshold
    // applies regardless of is_processing — the heartbeat measures poll round-trip
    // freshness, which behaves identically whether the session is idle or processing.
    const heartbeatMs = sessionPollHeartbeatAt[sid]
    if (!heartbeatMs) {
      // Issue #1960: same class of "detection state with no visible signal" as the core
      // bug — make the no-heartbeat path explicit and observable rather than a silent
      // no-op indistinguishable from "not stalled".
      pushDebugEvent('polling', 'stall-check-no-heartbeat', { sessionId: sid })
      return
    }

    const stallMs = Date.now() - heartbeatMs
    sessionStalled.value = stallMs >= STALL_TIMEOUT_MS
    pushDebugEvent('polling', 'stall-check', {
      sessionId: sid, stallMs, thresholdMs: STALL_TIMEOUT_MS, passed: stallMs < STALL_TIMEOUT_MS
    })
    if (stallMs < STALL_TIMEOUT_MS) return

    // Issue #1795: skip healing while a detected fetch error is already being retried by
    // the exponential-backoff loop — that failure mode self-heals; letting the watchdog
    // also intervene risks a redundant/racy reconnect. Liveness reporting above runs
    // unconditionally; only the heal action below is gated on sessionConnected.
    if (!sessionConnected.value) return

    // Cooldown: prevent heal storms
    if (Date.now() - lastHealedAt < HEAL_COOLDOWN_MS) return

    // Issue #1954: mutex guard — a heal already in flight for this session must not be
    // overlapped by a second one. This is checked (and set) in addition to, not instead of,
    // the cooldown above: the cooldown still spaces out fast-completing heals as before, while
    // this guard is what actually eliminates the overlap race regardless of heal duration.
    if (sessionHealInFlight[sid]) {
      pushDebugEvent('polling', 'stall-heal-skipped-inflight', { sessionId: sid })
      return
    }

    lastHealedAt = Date.now()
    sessionHealInFlight[sid] = true

    // Issue #1917 (Fix B1): bump the generation and abort the in-flight fetch synchronously,
    // before either await below. disconnectSession() does this too, but only after the two
    // awaited REST calls that follow — leaving a window where a visibilitychange-resumed
    // old-generation loop can still resolve and redeliver an already-processed event batch
    // while this heal sequence is still in flight.
    sessionPollGeneration++
    sessionAbortController?.abort()

    try {
      console.warn(`[stall-heal] Session ${sid} stalled ${Math.round(stallMs / 1000)}s (is_processing=${session.is_processing}); re-syncing`)
      pushDebugEvent('polling', 'stall-heal-start', { sessionId: sid, stallMs, isProcessing: session.is_processing })

      // Step 1: backfill any missed messages via REST (deduplicates by message ID)
      try {
        await messageStore.syncMessages(sid)
      } catch (err) {
        console.error('[stall-heal] syncMessages failed:', err)
      }

      // Step 2: re-fetch cursor and restart poll loop
      try {
        const result = await api.get(`/api/poll/session/${sid}/cursor`)
        sessionCursors[sid] = result?.cursor ?? 0
      } catch {
        sessionCursors[sid] = 0
      }

      // Guard: abort if the user switched sessions during the async operations above
      if (currentSessionId.value !== sid) {
        console.warn(`[stall-heal] Session ${sid} heal aborted — session changed during sync`)
        pushDebugEvent('polling', 'stall-heal-done', { sessionId: sid, aborted: true })
        return
      }
      await disconnectSession()
      await connectSession(sid)

      console.warn(`[stall-heal] Session ${sid} re-synced; resumed polling at cursor ${sessionCursors[sid]}`)
      pushDebugEvent('polling', 'stall-heal-done', { sessionId: sid, aborted: false, cursor: sessionCursors[sid] })
    } finally {
      delete sessionHealInFlight[sid]
    }
  }

  // ========== PAGE VISIBILITY ==========
  function setupVisibilityHandler() {
    if (visibilityUnsubscribe) visibilityUnsubscribe()

    const handler = () => {
      if (document.visibilityState === 'visible') {
        pushDebugEvent('polling', 'visibility-reconnect', { sessionId: currentSessionId.value })
        uiAbortController?.abort()
        sessionAbortController?.abort()
      }
    }
    document.addEventListener('visibilitychange', handler)
    visibilityUnsubscribe = () => {
      document.removeEventListener('visibilitychange', handler)
    }
  }

  // ========== OUTBOUND REST ACTIONS ==========
  async function sendMessage(content, metadata) {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    if (!sid) {
      console.error('Cannot send message: no current session')
      return { success: false, error: 'No active session' }
    }
    try {
      const payload = { message: content }
      if (metadata) payload.metadata = metadata
      await api.post(`/api/sessions/${sid}/messages`, payload)
      setTimeout(() => {
        import('./mcp').then(({ useMcpStore }) => {
          useMcpStore().fetchMcpStatus(sid)
        })
      }, 2000)
      return { success: true }
    } catch (err) {
      // Issue #1746 (stage: permissions) follow-up: surface the failure to the caller instead
      // of only logging — the caller (InputArea.vue) needs this to avoid silently discarding
      // the user's typed message on a rejected send.
      const message = err.data?.detail || err.message || 'Failed to send message'
      if (err.status === 409) {
        console.warn('Message rejected:', message)
      } else {
        console.error('Failed to send message:', err)
      }
      return { success: false, error: message }
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
          const priorState = priorSession?.state

          sessionStore.updateSession(payload.data.session_id, payload.data.session)

          const changedSessionId = payload.data.session_id
          const newState = payload.data.session.state

          if (newState === 'error') {
            console.log(`[UI state_change] Session ${changedSessionId} entered error state, reloading messages`)
            const messageStore = useMessageStore()
            messageStore.clearMessages(changedSessionId)
            // Fix 3: do NOT reset cursor — backend EventQueue survives error state,
            // cursor is still valid. Resetting it produced ?since=undefined (422 wedge).
            // Issue #1746 (stage: subagents): re-seed background-agent leg state BEFORE
            // replaying history — clearMessages() just wiped this session's taskLegsByTaskId,
            // and loadMessages() no longer reconstructs it from history (hydrateBackgroundAgents
            // is the sole source now). Without this, an error-state reload permanently loses
            // subagent leg/task_id state for the session.
            messageStore.hydrateBackgroundAgents(changedSessionId).then(() => {
              messageStore.loadMessages(changedSessionId)
            })
          }

          if (newState === 'active') {
            import('./mcp').then(({ useMcpStore }) => {
              const mcpStore = useMcpStore()
              mcpStore.fetchMcpStatus(changedSessionId)
            })
          }

          if (newState === 'error' && priorState !== 'error') {
            notify('session_error', { sessionName: payload.data.session.name || 'Session', sessionId: changedSessionId })
          }
          if (wasProcessing && !payload.data.session.is_processing) {
            notify('task_complete', { sessionName: payload.data.session.name || 'Session', sessionId: changedSessionId })
          }
          if (newState === 'paused' && priorState !== 'paused') {
            notify('permission_prompt', { sessionName: payload.data.session.name || 'Session', sessionId: changedSessionId })
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
          const editHistoryStore = useEditHistoryStore()
          editHistoryStore.clearHistory(resetSessionId)
          const uiStore = useUIStore()
          uiStore.setRateLimits(null)
          const sessionStore2 = useSessionStore()
          sessionStore2.recordSessionReset(resetSessionId)
          delete sessionCursors[resetSessionId]
          delete sessionPollHeartbeatAt[resetSessionId]
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
            fromMinion: payload.data.from_minion_name || 'Minion',
            sessionId: payload.data.session_id
          })
        }
        break

      case 'mcp_oauth_complete': {
        const serverId = payload.server_id
        if (serverId) {
          import('./mcpConfig').then(({ useMcpConfigStore }) => {
            const mcpStore = useMcpConfigStore()
            mcpStore.fetchOAuthStatus(serverId)
            // Issue #1387: complete any pending Reconnect flow for this server
            if (mcpStore.pendingReconnect.get(serverId)) {
              mcpStore.completeReconnect(serverId).then(() => {
                import('./secrets').then(({ useSecretsStore }) => {
                  useSecretsStore().fetchSecrets()
                })
              }).catch(e => console.error('[Reconnect] import-as-secret failed:', e))
            }
          })
        }
        break
      }

      case 'mcp_oauth_refreshed': {
        // Issue #976: Background refresh succeeded — update status indicator
        const serverId = payload.server_id
        if (serverId) {
          import('./mcpConfig').then(({ useMcpConfigStore }) => {
            useMcpConfigStore().fetchOAuthStatus(serverId)
          })
        }
        break
      }

      case 'secret_refreshed': {
        // Issue #1387: VaultRefreshManager background refresh succeeded
        const secretName = payload.secret_name
        if (secretName) {
          import('./secrets').then(({ useSecretsStore }) => {
            useSecretsStore().handleSecretRefreshed(secretName)
          })
        }
        break
      }

      case 'secret_refresh_failed': {
        // Issue #1387: VaultRefreshManager background refresh permanently failed
        const secretName = payload.secret_name
        if (secretName) {
          import('./secrets').then(({ useSecretsStore }) => {
            useSecretsStore().handleSecretRefreshFailed(secretName, payload.error || '')
          })
        }
        break
      }

      case 'secret_oauth_complete': {
        // Issue #1871: standalone vault-secret guided-authorization flow finished
        // (success or failure) — mirrors mcp_oauth_complete, but fires on failure
        // too since the settings panel has no other way to learn the outcome of a
        // flow completed in a cross-origin popup.
        if (payload.flow_id) {
          import('./secrets').then(({ useSecretsStore }) => {
            useSecretsStore().handleSecretOAuthComplete(payload)
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

      case 'schedule_monitor_error':
        import('./schedule').then(({ useScheduleStore }) => {
          const scheduleStore = useScheduleStore()
          scheduleStore.handleScheduleMonitorError(
            payload.legion_id || payload.data?.legion_id, payload
          )
        })
        break

      case 'session_restart_error':
        console.error(
          `[session_restart_error] Session ${payload.data?.session_id}: ${payload.data?.error}`
        )
        notify('session_restart_error', {
          sessionId: payload.data?.session_id,
          error: payload.data?.error,
        })
        break

      case 'rate_limits_update': {
        const uiStore = useUIStore()
        uiStore.setRateLimits(payload.data)
        break
      }

      case 'session_watchdog_alert': {
        const uiStore = useUIStore()
        uiStore.pushAlert(payload)
        notify('session_error', { sessionName: payload.session_name || 'Session', sessionId: payload.session_id })
        break
      }

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
        // Issue #1027: SDK status events carrying permission mode changes
        if (message.type === 'system' &&
            (message.subtype === 'permission_mode_change' || message.metadata?.subtype === 'permission_mode_change') &&
            message.metadata?.permission_mode) {
          sessionStore.updateSession(sessionId, { current_permission_mode: message.metadata.permission_mode })
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

      case 'link_registered':
        if (payload.link) {
          import('./links').then(({ useLinksStore }) => {
            useLinksStore().addLink(sessionId, payload.link)
          })
        }
        break

      case 'resource_removed':
        if (payload.resource_id) {
          const resourceStore = useResourceStore()
          resourceStore.handleResourceRemoved(sessionId, payload.resource_id)
        }
        break

      case 'queue_update': {
        const queueStore = useQueueStore()
        queueStore.handleQueueUpdate(sessionId, payload)
        break
      }

      case 'usage_updated': {
        import('./usage').then(({ useUsageStore }) => {
          useUsageStore().handleUsageUpdated(payload)
        })
        break
      }

      case 'context_update': {
        const { input_tokens, context_window, context_pct } = payload
        sessionStore.patchSession(sessionId, {
          context_input_tokens: input_tokens,
          context_window: context_window,
          context_pct: context_pct,
        })
        break
      }

      // Issue #1486: streaming text delta — forward to message store for RAF-batched mutation
      case 'assistant_delta':
        messageStore.handleAssistantDelta(sessionId, payload.data)
        break

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
    sessionStalled,
    currentSessionId,
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
    resetSessionCursor,
    checkSessionStall,
    sendMessage,
    sendPermissionResponse,
    sendPermissionResponseWithInput,
    interruptSession,
    connectLegion,
    disconnectLegion,
  }
})
