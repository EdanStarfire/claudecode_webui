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
  // Issue #1974: per-session snapshot of totalFrozenMs taken at the last heartbeat write,
  // so checkSessionStall() can forgive only the frozen time that occurred *after* that
  // heartbeat (see totalFrozenMs below) rather than the session's entire frozen history.
  const frozenMsAtHeartbeat = {}

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
  // Issue #1974: tick-lateness watchdog. A backgrounded tab or sleeping machine can
  // freeze the JS timer loop itself, so the stall-check interval firing late is the
  // signal that wall-clock time passed without this page being able to observe it —
  // any cause (background throttling, machine sleep, long GC/debugger pauses) shows up
  // the same way. Excess over this buffer (on top of the expected STALL_CHECK_INTERVAL_MS
  // gap) is treated as frozen time and forgiven from the staleness computation below.
  const TICK_LATE_BUFFER_MS = 2000
  // Issue #1974 follow-up: bounds how much of any *single* tick's lateness can be
  // forgiven. Without this, an unusually large single gap (a stuck debugger, an
  // extreme power-saving suspend, or any other pathological freeze) would be forgiven
  // in full no matter its size, permanently masking a connection that died during it.
  //
  // What this cap does NOT fix: sustained background-tab throttling (Chrome clamps
  // recurring ticks to roughly once per minute once hidden) still produces many
  // separate ~60s gaps, each individually far under this 30-minute cap — so each is
  // still forgiven in full, unchanged from before this cap existed. A connection that
  // dies while the tab stays backgrounded is therefore still detected only ~6x slower
  // than STALL_TIMEOUT_MS while hidden (roughly minutes, not the normal 40s) — this cap
  // only bounds a single outsized gap, not that cumulative, repeated-small-gap case.
  //
  // What this cap deliberately trades off: a single legitimate freeze *longer* than 30
  // minutes (an extended meeting, a long commute, an overnight sleep) will read as
  // stalled and attempt a heal the moment it's next observed, even if the connection
  // was actually fine the whole time — reproducing, at a much higher and rarer
  // threshold, a milder form of the exact false-positive this feature exists to
  // prevent. 30 minutes is chosen to comfortably clear ordinary short breaks/naps (so
  // the common case stays fully protected) while still bounding the absolute worst
  // case to a finite, provable delay instead of indefinite, unlimited forgiveness.
  const MAX_FORGIVE_PER_TICK_MS = 30 * 60 * 1000
  let lastTickAt = 0
  let totalFrozenMs = 0

  // Page Visibility cleanup
  let visibilityUnsubscribe = null

  // ========== COMPUTED ==========
  const overallStatus = computed(() => {
    const uiStore = useUIStore()
    // Issue #1977 (AC4): a connected transport with a failed initial data load must
    // never read as fully 'connected' — reuses 'partial' (already means "not fully
    // healthy") rather than adding a new value only this one case would use.
    if (uiConnected.value && uiStore.appDataStatus === 'failed') {
      return 'partial'
    }
    if (uiConnected.value && (sessionConnected.value || !currentSessionId.value)) {
      return 'connected'
    }
    if (uiConnected.value) {
      return 'partial'
    }
    return 'disconnected'
  })

  // ========== HELPERS ==========
  // Issue #1974: single write path for a successful-poll heartbeat, so
  // sessionPollHeartbeatAt and its paired frozenMsAtHeartbeat snapshot can never drift
  // out of sync at a call site that forgets one of the two.
  function seedHeartbeat(sessionId) {
    sessionPollHeartbeatAt[sessionId] = Date.now()
    frozenMsAtHeartbeat[sessionId] = totalFrozenMs
  }

  // Issue #1974: single deletion path for all four per-session polling maps, shared by
  // resetSessionCursor(), cleanupSessionPollingState(), and the session_reset event
  // handler below — so a future 5th per-session map only needs to be added here once,
  // instead of at every call site independently (one of those call sites had already
  // drifted out of sync with the others before this helper existed).
  function clearSessionPollingKeys(sessionId) {
    delete sessionCursors[sessionId]
    delete sessionPollHeartbeatAt[sessionId]
    delete sessionHealInFlight[sessionId]
    delete frozenMsAtHeartbeat[sessionId]
  }

  function getPollUrl(path, cursor, timeout = 30) {
    const token = getAuthToken()
    const base = `${path}?since=${cursor}&timeout=${timeout}`
    return token ? `${base}&token=${encodeURIComponent(token)}` : base
  }

  // ========== APP DATA LOAD (Issue #1977) ==========
  // In-flight guard so overlapping calls (initial mount + a reconnect racing it) collapse
  // into one shared load instead of firing duplicate fetches.
  let appDataLoadPromise = null

  function loadAppData() {
    if (appDataLoadPromise) return appDataLoadPromise

    const uiStore = useUIStore()
    const projectStore = useProjectStore()
    const sessionStore = useSessionStore()
    uiStore.setAppDataStatus('loading')

    appDataLoadPromise = Promise.allSettled([
      projectStore.fetchProjects(),
      sessionStore.fetchSessions(),
    ]).then((results) => {
      const failed = results.some(r => r.status === 'rejected')
      uiStore.setAppDataStatus(failed ? 'failed' : 'loaded')
      return results
    }).finally(() => {
      appDataLoadPromise = null
    })

    return appDataLoadPromise
  }

  // Retries a still-failed app-data load and/or a still-relevant transient deep-link
  // failure. Shared by the reconnect-triggered trigger below and the periodic watcher
  // right after it — both are just different ways of deciding *when* to call this.
  function retryStaleAppData() {
    const uiStore = useUIStore()
    if (uiStore.appDataStatus !== 'loaded') {
      loadAppData()
    }
    const failure = uiStore.deepLinkFailure
    // Only retry if the user is still trying to view the session that failed — if
    // they've since navigated elsewhere, currentSessionId will no longer match (and
    // session.js's selectSession() already clears deepLinkFailure on navigation away),
    // so this naturally skips rather than hijacking their current view.
    if (failure?.kind === 'transient') {
      const sessionStore = useSessionStore()
      if (sessionStore.currentSessionId === failure.sessionId) {
        // selectSession() early-returns as a no-op when sessionId is already
        // currentSessionId and no selection is in flight — both true here, since the
        // failed attempt set currentSessionId optimistically before failing. Clearing
        // it first forces a real re-fetch, mirroring the same bypass SessionManageModal.vue
        // uses to force a reconnect after a session restart.
        sessionStore.currentSessionId = null
        // Fire-and-forget (same pattern as SessionManageModal.vue's restart-reconnect) —
        // caught here only to avoid an unhandled-rejection console error if it fails
        // again; the failure itself is already reflected via deepLinkFailure/appDataStatus.
        sessionStore.selectSession(failure.sessionId).catch(() => {})
      }
    }
  }

  // Issue #1977 (AC7): the reconnect-triggered retry (inside startUIPolling()'s catch
  // block, below) only fires when the /api/poll/ui transport itself errors. But
  // /api/projects, /api/sessions, and a deep-linked session's own fetch are independent
  // backend routes that can fail while /api/poll/ui stays healthy throughout — without
  // this, appDataStatus could stay 'failed' forever with LoadStatusBanner's "retrying
  // automatically" claim never actually true. This periodic watcher is the catch-all:
  // while UI polling is active, retry on a fixed cadence regardless of why the load
  // previously failed. loadAppData()'s own in-flight guard means this can't double-fetch
  // if a reconnect-triggered retry is already in flight.
  const APP_DATA_RETRY_INTERVAL_MS = 15000
  let appDataRetryInterval = null

  function startAppDataRetryWatcher() {
    if (appDataRetryInterval) return
    appDataRetryInterval = setInterval(retryStaleAppData, APP_DATA_RETRY_INTERVAL_MS)
  }

  function stopAppDataRetryWatcher() {
    if (appDataRetryInterval) {
      clearInterval(appDataRetryInterval)
      appDataRetryInterval = null
    }
  }

  // ========== UI POLL LOOP ==========
  async function startUIPolling(initialCursor = 0) {
    if (uiConnected.value) return
    uiCursor = initialCursor
    uiConnected.value = true
    uiRetryCount.value = 0
    startAppDataRetryWatcher()
    const myGeneration = ++uiPollGeneration
    // Issue #1977: tracks "we backed off after an error and are waiting to confirm
    // we're actually back" — retryStaleAppData() fires once, on the next CONFIRMED
    // successful response, not merely once the backoff delay elapses. Firing on backoff
    // alone would re-fire on every retry tick during a sustained total outage (each
    // subsequent fetch attempt still failing), roughly doubling REST traffic against a
    // server that's already known unreachable.
    let recoveringFromError = false

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
        if (recoveringFromError) {
          recoveringFromError = false
          // Issue #1977 (AC3, AC7): a confirmed successful poll after an outage means
          // the connection is genuinely back — retry anything that didn't survive that
          // outage immediately, rather than waiting for the periodic watcher's next tick.
          retryStaleAppData()
        }

        if (data.events && data.events.length > 0) {
          for (const event of data.events) {
            handleUIMessage(event)
          }
        }
        pushDebugEvent('polling', 'poll-cycle', {
          stream: 'ui', cursorBefore: uiCursor, cursorAfter: data.next_cursor, generation: myGeneration
        })
        if (data.reset) {
          pushDebugEvent('polling', 'poll-reset', {
            stream: 'ui', staleCursor: uiCursor, next_cursor: data.next_cursor, eventCount: data.events?.length ?? 0, generation: myGeneration
          })
        }
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
          recoveringFromError = true
        }
      }
    }
  }

  function stopUIPolling() {
    uiPollGeneration++
    uiConnected.value = false
    uiAbortController?.abort()
    uiAbortController = null
    stopAppDataRetryWatcher()
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
        // Issue #1795/#1974: any successful response — even one carrying zero events
        // after the server's long-poll hold — proves this connection is alive. But
        // only for the CURRENT generation/session: an in-flight response can still
        // land after sessionPollGeneration was bumped and the fetch aborted (e.g. a
        // stall-heal reconnecting to this same session while this response was already
        // past the fetch, mid `await response.json()`, so the abort had no effect). An
        // in-flight response from a since-superseded generation must not write the
        // heartbeat, its paired frozen-time snapshot (Issue #1974), or clear
        // sessionStalled — doing so would silently reset the staleness/forgiveness
        // baseline for a session that may still actually be stalled, right after a
        // heal that hasn't itself delivered anything yet.
        if (sessionPollGeneration === myGeneration && currentSessionId.value === sessionId) {
          seedHeartbeat(sessionId)
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
        if (data.reset) {
          pushDebugEvent('polling', 'poll-reset', {
            stream: 'session', sessionId, staleCursor: cursor, next_cursor: data.next_cursor, eventCount: data.events?.length ?? 0, generation: myGeneration
          })
        }
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

  async function connectSession(sessionId, { isRecoveryReconnect = false } = {}) {
    // Stop any existing session poll (Fix 2: truly awaits loop exit)
    await disconnectSession()

    const myGeneration = ++sessionPollGeneration  // Fix 1: generation guard
    currentSessionId.value = sessionId
    sessionConnected.value = true
    sessionRetryCount.value = 0
    // Issue #1960: prevent a previously-viewed session's stalled flag from leaking
    // onto the newly-selected session.
    // Issue #1973: skip this for a stall-heal recovery reconnect — reconnecting is not
    // itself evidence of recovery, so a still-stalled session must stay visibly stalled
    // until a real poll response clears the flag (below, in _runSessionPollLoop).
    if (!isRecoveryReconnect) {
      sessionStalled.value = false
    }

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
    // Issue #1973: skip this on a recovery reconnect — the existing baseline is legitimately
    // stale, and reseeding it here would forge liveness before any response has actually
    // arrived. Only a real poll response (below) should advance it in that case.
    if (!isRecoveryReconnect) {
      seedHeartbeat(sessionId)
    }

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
    clearSessionPollingKeys(sessionId)
    // Issue #1960: prevent a reset session's stalled flag from leaking onto whatever
    // session is current afterward.
    sessionStalled.value = false
  }

  // Issue #1974: full teardown of a permanently-deleted session's per-session polling
  // state, called from session.js's deleteSession(). Unlike resetSessionCursor() above
  // (always the single current session being reset/restarted by the user), deleteSession()
  // can cascade-delete many sessions at once, most of which are NOT the current session —
  // so this must only clear sessionStalled when the deleted session actually is the one
  // currently displayed, or it would wrongly clear a genuinely-stalled current session's
  // flag whenever an unrelated sibling session gets deleted.
  function cleanupSessionPollingState(sessionId) {
    clearSessionPollingKeys(sessionId)
    if (currentSessionId.value === sessionId) {
      sessionStalled.value = false
    }
  }

  // ========== STALL DETECTOR (Fix 5) ==========
  function startStallDetector() {
    if (stallDetectorInterval) return
    // Issue #1974: (re)seed the tick-lateness baseline whenever the detector (re)starts,
    // so a gap that occurred before this connectSession() call (e.g. while disconnected)
    // isn't misread as a freeze on the very first tick.
    lastTickAt = Date.now()
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
    // Issue #1974: tick-lateness measurement — page-level, independent of which session
    // is current (a freeze affects the whole tab's ability to run JS, not just polling
    // for the active session), so this runs before the sid early-return below. Any
    // excess over the expected STALL_CHECK_INTERVAL_MS gap (past a small jitter buffer)
    // means that much wall-clock time was not actually observable by the page.
    const now = Date.now()
    const observedGap = now - lastTickAt
    const uncappedFrozenMs = Math.max(0, observedGap - STALL_CHECK_INTERVAL_MS - TICK_LATE_BUFFER_MS)
    const frozenMs = Math.min(uncappedFrozenMs, MAX_FORGIVE_PER_TICK_MS)
    if (frozenMs > 0) {
      totalFrozenMs += frozenMs
      pushDebugEvent('polling', 'freeze-detected', {
        frozenMs, totalFrozenMs, observedGap, capped: uncappedFrozenMs > MAX_FORGIVE_PER_TICK_MS
      })
    }
    lastTickAt = now

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

    // Issue #1974: forgive only the frozen time that occurred *after* the last successful
    // heartbeat — a stall that was already accumulating before a freeze started is
    // untouched and still crosses the threshold on schedule.
    const frozenSinceHeartbeat = totalFrozenMs - (frozenMsAtHeartbeat[sid] ?? 0)
    const stallMs = Math.max(0, (now - heartbeatMs) - frozenSinceHeartbeat)
    sessionStalled.value = stallMs >= STALL_TIMEOUT_MS
    pushDebugEvent('polling', 'stall-check', {
      sessionId: sid, stallMs, thresholdMs: STALL_TIMEOUT_MS, passed: stallMs < STALL_TIMEOUT_MS, frozenSinceHeartbeat
    })
    if (stallMs < STALL_TIMEOUT_MS) return

    // Issue #1795: skip healing while a detected fetch error is already being retried by
    // the exponential-backoff loop — that failure mode self-heals; letting the watchdog
    // also intervene risks a redundant/racy reconnect. Liveness reporting above runs
    // unconditionally; only the heal action below is gated on sessionConnected.
    if (!sessionConnected.value) return

    // Issue #1974: a backgrounded tab must not itself trigger recovery — gate only the
    // heal step, not the staleness computation/reporting above, so sessionStalled stays
    // accurate the instant the user returns (established by an actual poll response, not
    // assumed on visibility-restore). Deliberately does not gate on the sleeping-machine
    // case (document.hidden can still read false there) — that's covered by the
    // tick-lateness forgiveness above alone, independent of visibility.
    if (document.hidden) {
      pushDebugEvent('polling', 'stall-heal-skipped-hidden', { sessionId: sid, stallMs })
      return
    }

    // Cooldown: prevent heal storms
    if (now - lastHealedAt < HEAL_COOLDOWN_MS) return

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
      await connectSession(sid, { isRecoveryReconnect: true })

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
          // Deliberately not routed through clearSessionPollingKeys()/sessionHealInFlight:
          // unlike resetSessionCursor()'s and cleanupSessionPollingState()'s callers (which
          // always disconnect the poll loop first, aborting any in-flight heal via its own
          // currentSessionId check), this handler reacts to an async server event that can
          // arrive at any time, including genuinely mid-heal for this exact session —
          // clearing the #1954 mutex here could let a second heal start concurrently with
          // one still finishing.
          delete sessionCursors[resetSessionId]
          // Issue #1979: if a stall-heal reconnect is actively in flight for this exact
          // session, its heartbeat is deliberately left stale (not yet reseeded) so
          // checkSessionStall() keeps reporting the genuine stall until a real poll
          // response lands. Deleting it here would erase that signal and make the
          // indicator transiently read healthy mid-heal. Skip this piece only — every
          // other part of session_reset's handling above/below still runs unconditionally.
          if (!sessionHealInFlight[resetSessionId]) {
            delete sessionPollHeartbeatAt[resetSessionId]
            delete frozenMsAtHeartbeat[resetSessionId]
            // Issue #1960: prevent a reset session's stalled flag from leaking onto whatever
            // session is current afterward — mirrors resetSessionCursor()'s same guard.
            sessionStalled.value = false
          }
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

      case 'session_deleted':
        if (payload.data?.session_id) {
          sessionStore.removeSessionsFromStores([payload.data.session_id]).then((wasCurrentSessionRemoved) => {
            if (wasCurrentSessionRemoved) {
              import('../router').then(({ default: router }) => {
                router.push('/')
              })
            }
          })
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
    loadAppData,
    startUIPolling,
    stopUIPolling,
    setupVisibilityHandler,
    connectSession,
    disconnectSession,
    resetSessionCursor,
    cleanupSessionPollingState,
    checkSessionStall,
    sendMessage,
    sendPermissionResponse,
    sendPermissionResponseWithInput,
    interruptSession,
    connectLegion,
    disconnectLegion,
  }
})
