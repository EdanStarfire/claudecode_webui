import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session'
import { useProjectStore } from './project'
import { useMessageStore } from './message'
import { useBackgroundTasksStore } from './backgroundTasks'
import { useTaskStore } from './task'
import { useResourceStore } from './resource'
import { useQueueStore } from './queue'
import { useUIStore } from './ui'
import { useEditHistoryStore } from './editHistory'
import { useMcpStore } from './mcp'
import { useMcpConfigStore } from './mcpConfig'
import { useSecretsStore } from './secrets'
import { useScheduleStore } from './schedule'
import { useLinksStore } from './links'
import { useUsageStore } from './usage'
import router from '@/router'
import { notify } from '@/composables/useNotifications'
import { getAuthToken, api, triggerReauth } from '@/utils/api'
import { pushDebugEvent } from '@/composables/useDebugBuffer'

// ========== STREAM CONNECTION STATE MACHINE (Epic #1990 stage 5b, consolidated stage 5c) ==========
// ONE state machine for the single multiplexed transport (epic #1990 stage 5c collapsed the
// former uiConn/sessionConn pair into one connection, since there's genuinely only one HTTP
// long-poll cycle now — UI and every subscribed session ride the same request/response).
// Liveness is derived ONLY from poll responses and fetch outcomes — a successful response
// (including an empty one after the server's long-poll hold) proves the connection is alive, a
// thrown fetch error proves it isn't. No wall-clock staleness timer exists anywhere in this design.
//
// States: idle | connecting | connected | resyncing | backoff | unreachable | archived
// `transition()` is the single function that ever assigns `.state` — every poll-loop branch
// calls it, nothing else writes state directly. This is both the one-reducer requirement and
// the single instrumentation point (every GENUINE state change emits a 'state-transition'
// debug event — issue #2161 AC5 gates this on next !== from, so a same-state no-op call does
// not; state assignment/hasEverConnected/onTransition still run unconditionally regardless).
//
// Issue #2160 AC6 — the transition table `connectedFamilyTarget()` and other comments below
// refer back to (matching the matrix `polling.test.js`'s "StreamConnection transition table"
// describe block actually encodes):
//
//   idle            -> connecting   connect() (connectSession()/startPolling() start the
//                                    shared loop if it isn't already running)
//   connecting      -> connected    poll response OK, no reset/evicted signal in THIS
//                                    response, backend_status !== 'unreachable'
//   connecting      -> unreachable  poll response OK, no reset/evicted signal in THIS
//                                    response, backend_status === 'unreachable'
//   connected       -> connected    poll response OK, no reset/evicted for this stream
//                                    (backend_status === 'degraded' included — decision 1:
//                                    a non-overriding annotation, not a state of its own)
//   connected       -> unreachable  poll response OK, backend_status === 'unreachable'
//   unreachable     -> connected    poll response OK, backend_status no longer 'unreachable'
//   connecting/
//   connected/
//   unreachable     -> resyncing    this stream's batch carries a stream_reset/evicted signal
//                                    for the CURRENT session (or the UI stream, for a
//                                    "Backend restart made explicit") — _processUIBatch()/
//                                    _processSessionBatch() call conn.transition('resyncing')
//                                    directly, unconditional on the state at the time (so this
//                                    fires even on the very first poll cycle after connect(),
//                                    before the end-of-cycle connectedFamilyTarget() transition
//                                    below has ever run once — a reset racing a session's very
//                                    first long-poll response is a real, reachable race, not
//                                    just a steady-state one)
//   resyncing       -> connected    the resync (loadMessages()/loadAppData()) resolved and
//                                   connectedFamilyTarget() says backend_status isn't
//                                   'unreachable'
//   resyncing       -> unreachable  the resync resolved but backend_status is 'unreachable'
//   connecting/
//   connected/
//   resyncing/
//   unreachable     -> backoff      a fetch() call (or the resync's own loadMessages()/
//                                   loadAppData() call) threw anything other than AbortError;
//                                   retryCount increments
//   backoff         -> connecting  the scheduled backoff delay elapsed; retry
//   *               -> idle        disconnect() (disconnectSession()/stopPolling()) — always
//                                  synchronous: aborts any in-flight fetch, clears any pending
//                                  backoff timer, bumps the generation so a still-in-flight
//                                  stale cycle's own eventual response is ignored
//
// Not a `conn` transition at all: an in-flight fetch() rejecting with AbortError (a visibility-
// resume or a newly-touched stream interrupting a stale cycle) leaves `.state` untouched and
// does not bump retryCount — "cancelled", not "failed".
//
// `archived` is layered independently on top of whatever `conn.state` is doing underneath —
// see `sessionStateRef`'s own computation just below `createStreamConnection()` — not a value
// `transition()` itself ever produces. `markSessionArchived()` sets it for the CURRENT
// session's view only; only connectSession()/disconnectSession() clear it. `conn` can never
// produce or revert this override on its own, by design (an archived view must never flip back
// live just because a stale in-flight poll response happens to land).
function createStreamConnection(kind, onTransition) {
  const state = ref('idle')
  // 'ok' | 'degraded' | 'unreachable' — the single multiplexed response's own backend_status
  // field (one value now, not one per stream — there's only one response per poll cycle).
  const backendStatus = ref('ok')
  const retryCount = ref(0)
  // True once this connection has reached 'connected' at least once since its last connect()/
  // disconnect() cycle.
  const hasEverConnected = ref(false)

  let abortController = null
  let generation = 0
  let backoffTimerHandle = null
  let backoffResolve = null

  function transition(next, sessionId) {
    const from = state.value
    state.value = next
    if (next === 'connected') hasEverConnected.value = true
    // Issue #2161 (AC5): a connected -> connected no-op poll cycle (idle steady state)
    // called this unconditionally before, logging a debug event on every single poll
    // cycle. Only an actual state change is debug-worthy; state assignment,
    // hasEverConnected, and onTransition below all still run unconditionally regardless.
    if (next !== from) {
      pushDebugEvent('polling', 'state-transition', { kind, sessionId: sessionId ?? null, from, to: next })
    }
    // Epic #1990 stage 5c: lets the store keep its own derived, PLAIN (not computed())
    // sessionState ref in sync with this — the single place `.state` ever changes.
    // Plain refs (rather than computed() over the subscriptions Map, which Vue's
    // reactivity can't track since it's an ordinary, non-reactive Map) both preserve
    // genuine reactivity for real consumers and stay directly test-assignable, matching
    // every other exported field here (uiState, sessionRetryCount, etc. are the same
    // underlying refs handed out directly).
    onTransition?.(next, sessionId)
  }

  function bumpGeneration() {
    generation += 1
    return generation
  }

  function isCurrentGeneration(gen) {
    return generation === gen
  }

  // Resolves any in-flight backoff sleep immediately (used by disconnect() so a sleeping
  // loop wakes up and observes the new generation/idle state instead of hanging until its
  // full delay elapses).
  function clearBackoffTimer() {
    if (backoffTimerHandle) {
      clearTimeout(backoffTimerHandle)
      backoffTimerHandle = null
    }
    if (backoffResolve) {
      const resolve = backoffResolve
      backoffResolve = null
      resolve()
    }
  }

  function scheduleBackoff(delayMs) {
    return new Promise(resolve => {
      backoffResolve = resolve
      backoffTimerHandle = setTimeout(() => {
        backoffTimerHandle = null
        backoffResolve = null
        resolve()
      }, delayMs)
    })
  }

  function connect() {
    retryCount.value = 0
    hasEverConnected.value = false
    const gen = bumpGeneration()
    transition('connecting')
    return gen
  }

  // Synchronous teardown: abort any in-flight fetch, clear any pending backoff sleep, bump
  // the generation, and go idle. No awaited exit-budget needed.
  function disconnect() {
    clearBackoffTimer()
    abortController?.abort()
    abortController = null
    bumpGeneration()
    retryCount.value = 0
    hasEverConnected.value = false
    backendStatus.value = 'ok'
    transition('idle')
  }

  function newAbortController() {
    abortController = new AbortController()
    return abortController
  }

  function abortInFlight() {
    abortController?.abort()
  }

  return {
    state,
    backendStatus,
    retryCount,
    hasEverConnected,
    newAbortController,
    abortInFlight,
    transition,
    connect,
    disconnect,
    bumpGeneration,
    isCurrentGeneration,
    scheduleBackoff,
  }
}

export const usePollingStore = defineStore('polling', () => {
  // ========== STATE ==========
  // sessionState/sessionHasEverConnected are PLAIN refs (not computed()) kept in sync
  // explicitly at every point their inputs change — see _syncSessionState()/
  // _syncSessionHasEverConnected() below. A computed() here would be invisible to Vue's
  // reactivity system for the input that actually flips most often (a flag on a plain
  // object inside `subscriptions`, an ordinary non-reactive Map, not a reactive one) and
  // would also stop being directly assignable the way every other exported field here
  // (uiState, sessionRetryCount, ...) already is — a pattern existing component tests
  // (InputArea.test.js, HeaderRow1.test.js) depend on.
  const sessionStateRef = ref('idle')
  const sessionHasEverConnectedRef = ref(false)

  function _syncSessionState() {
    sessionStateRef.value = isCurrentViewArchived.value ? 'archived' : conn.state.value
  }
  function _syncSessionHasEverConnected() {
    sessionHasEverConnectedRef.value = subscriptions.get(currentSessionId.value)?.hasEverSeenResponse ?? false
  }

  const conn = createStreamConnection('multiplex', () => _syncSessionState())

  // The session the current view is addressing — null when no session has ever been
  // selected/archived-viewed in this tab. Used for outbound REST addressing (sendMessage etc,
  // via sessionStore.currentSessionId, independently) and InputArea's gating boundary.
  const currentSessionId = ref(null)
  // True while the current view is an archived/read-only snapshot — archived views never
  // subscribe to live polling (decision 5, unchanged from stage 5b). Independent of `conn`'s
  // own transport state by construction, so a stale in-flight poll response can never revert
  // an archived view back to "connected" underneath it.
  const isCurrentViewArchived = ref(false)
  const currentLegionId = ref(null)  // stub - always null
  const legionConnected = ref(false)  // stub - always false
  const legionRetryCount = ref(0)    // stub

  let uiCursor = 0

  // Issue #2161: one-shot fast-probe flag. True for exactly the next request built by
  // startPolling()'s loop after a fresh connect() or a subscription-set change detected
  // by interruptIfStale() — that one request sends timeout:0 instead of timeout:30 so the
  // input doesn't stay gated behind a 30s long-poll it has no reason to wait out. Read and
  // cleared to false SYNCHRONOUSLY in the request-build block, before the await fetch(...),
  // so it can never cause a busy-poll: the only way it becomes true again is a fresh
  // connect() or a fresh genuine subscription change.
  let fastProbePending = true

  // Epic #1990 stage 5c: subscription-set model replacing sessionCursors' passive cache.
  // Map<sessionId, {cursor: number, hasEverSeenResponse: boolean}> — every entry here is
  // ACTIVELY polled every cycle (not just cached), so a recently-viewed-but-not-current
  // session keeps receiving live events via dispatchEvent() the whole time it's subscribed
  // (US2) instead of only resuming from a remembered cursor on switch-back.
  const subscriptions = new Map()
  // Most-recently-touched session ids, front = most recent. Bounds `subscriptions`' size —
  // current + MAX_SUBSCRIBED_SESSIONS-1 most-recently-viewed stay subscribed; the rest age out.
  const lruSessionIds = []
  const MAX_SUBSCRIBED_SESSIONS = 5

  // Issue #2140: resync (evicted/stream_reset) uses the same per-page-inactivity/abort/
  // resume machinery as the initial hydration load — see message.js's
  // claimHydrationLoad()/fetchAllMessagePages() and session.js's identically-valued
  // constant of the same name.
  const HISTORY_PAGE_INACTIVITY_TIMEOUT_MS = 30000
  // After this many CONSECUTIVE resync failures for a session, stop auto-retriggering it
  // from the poll loop (today's literally-unbounded retry loop for this failure path) —
  // the stall banner becomes terminal until the user clicks Retry (retryResync()).
  const RESYNC_FAILURE_CEILING = 5
  // sessionId -> { active: true, terminal: boolean, error: string } — only ever written on
  // an actual resync timeout/throw (never merely "in flight"), so a normal fast resync never
  // flickers this banner. A separate map from hydrationStageBySession (session.js), which is
  // semantically "pre-ready" and gets deleted once a session first reaches ready — a resync
  // happens to an already-ready, already-displayed session.
  const resyncStallBySession = ref(new Map())
  // sessionId -> consecutive failure count. Non-reactive internal bookkeeping only (same
  // pattern as message.js's loadedEventCursors) — nothing renders off the raw count, only
  // the derived `terminal` flag stored in resyncStallBySession above.
  const resyncAttemptsBySession = new Map()
  // Snapshot of session_id -> cursor the CURRENTLY in-flight (or most recently built)
  // request's body was built from — compared against to decide whether a subscription
  // change needs to interrupt an in-flight long-poll (see interruptIfStale()'s docstring;
  // mirrors shared/poll_relay.py's identical `_active_subscription`/cancellation mechanism).
  // A Map (not a Set) because set-membership alone misses one real case: a session
  // disconnected and immediately re-subscribed with a DIFFERENT starting cursor while the
  // OLD in-flight request (keyed by the same session_id, now-stale cursor) is still
  // pending — the id "isn't new" by membership, but the in-flight request is answering
  // the wrong question for it regardless.
  let activeSessionCursors = new Map()

  // Page Visibility cleanup
  let visibilityUnsubscribe = null

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

  // Issue #1977 (AC7): the reconnect-triggered retry (inside startPolling()'s catch
  // block, below) only fires when the /api/poll transport itself errors. But
  // /api/projects, /api/sessions, and a deep-linked session's own fetch are independent
  // backend routes that can fail while /api/poll stays healthy throughout — without
  // this, appDataStatus could stay 'failed' forever with LoadStatusBanner's "retrying
  // automatically" claim never actually true. This periodic watcher is the catch-all:
  // while polling is active, retry on a fixed cadence regardless of why the load
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

  // Compute the "poll succeeded, not mid-resync" target state from the response's own
  // backend_status. Per the transition table above createStreamConnection(): backend_status
  // === 'unreachable' always wins, otherwise plain connected. `degraded` deliberately does
  // NOT divert from this — it's an independent, non-overriding annotation carried on
  // backendStatus alone.
  function connectedFamilyTarget() {
    return conn.backendStatus.value === 'unreachable' ? 'unreachable' : 'connected'
  }

  // ========== SUBSCRIPTION-SET MANAGEMENT (epic #1990 stage 5c) ==========
  // Registers/refreshes interest in a session's stream and keeps the LRU bounded to
  // MAX_SUBSCRIBED_SESSIONS. Does NOT decide interruption itself — callers that know the
  // session's final cursor (connectSession()) call interruptIfStale() once that's settled;
  // see its docstring for why set-membership alone isn't a sufficient interrupt condition.
  function touchSubscription(sessionId) {
    const idx = lruSessionIds.indexOf(sessionId)
    if (idx !== -1) lruSessionIds.splice(idx, 1)
    lruSessionIds.unshift(sessionId)

    if (!subscriptions.has(sessionId)) {
      subscriptions.set(sessionId, { cursor: 0, hasEverSeenResponse: false })
    }

    while (lruSessionIds.length > MAX_SUBSCRIBED_SESSIONS) {
      const evictedId = lruSessionIds.pop()
      subscriptions.delete(evictedId)
    }
  }

  // If this session's desired cursor isn't what the poll loop's CURRENTLY in-flight (or
  // most recently built) request actually asked Backend for, interrupt it immediately —
  // without this, a newly subscribed (or disconnect-then-resubscribed-with-a-different-
  // cursor) session would have to wait out the in-flight request's full long-poll timeout
  // before the loop even asks about it with the right cursor (the exact regression found
  // and fixed on the relay side in phase 2; the browser's single poll loop has the
  // identical failure mode for the identical reason, since it too starts polling long
  // before any specific session is ever subscribed).
  function interruptIfStale(sessionId, cursor) {
    if (activeSessionCursors.get(sessionId) !== cursor) {
      // Issue #2161 (AC1/T2): a genuine subscription-set change always re-arms the
      // fast-probe, regardless of conn.state.value — including the case where this
      // runs before the loop has even started (startPolling()'s own connect() below
      // then simply finds the flag already true, a harmless no-op double-set).
      fastProbePending = true
      if (conn.state.value !== 'idle') {
        conn.abortInFlight()
      }
    }
  }

  // Full eviction — used when a session should stop being polled entirely (deleted, or an
  // explicit disconnect flow that wants a clean slate before reconnecting), as opposed to
  // merely aging out of the LRU on its own.
  function evictSubscription(sessionId) {
    lastBatchBySession.delete(sessionId)
    subscriptions.delete(sessionId)
    const idx = lruSessionIds.indexOf(sessionId)
    if (idx !== -1) lruSessionIds.splice(idx, 1)
    if (sessionId === currentSessionId.value) _syncSessionHasEverConnected()
    // Issue #2140: a deleted/disconnected session has no business carrying a stale resync
    // stall/failure count forward to whatever this session id means next.
    resyncAttemptsBySession.delete(sessionId)
    if (resyncStallBySession.value.has(sessionId)) {
      resyncStallBySession.value.delete(sessionId)
      resyncStallBySession.value = new Map(resyncStallBySession.value)
    }
  }

  // ========== SINGLE MULTIPLEXED POLL LOOP ==========
  async function startPolling(initialCursor = 0) {
    if (conn.state.value !== 'idle') return
    uiCursor = initialCursor
    startAppDataRetryWatcher()
    const myGeneration = conn.connect()
    // Issue #2161 (AC1): covers "first request after connect" — including the very
    // first UI-only request before any session is ever subscribed.
    fastProbePending = true
    // Issue #1977: tracks "we backed off after an error and are waiting to confirm we're
    // actually back" — retryStaleAppData() fires once, on the next CONFIRMED successful
    // response, not merely once the backoff delay elapses.
    let recoveringFromError = false

    while (conn.state.value !== 'idle') {
      activeSessionCursors = new Map([...subscriptions.entries()].map(([sid, sub]) => [sid, sub.cursor]))
      try {
        const controller = conn.newAbortController()
        // Issue #2161: consume the one-shot fast-probe flag for THIS cycle's request,
        // clearing it synchronously before the await fetch(...) below — a concurrent
        // interruptIfStale() call (from a click handler) re-arms it correctly for
        // whichever request runs next rather than racing this one.
        const useFastProbe = fastProbePending
        fastProbePending = false
        const body = {
          timeout: useFastProbe ? 0 : 30,
          ui: { since: uiCursor },
          sessions: Object.fromEntries(
            [...subscriptions.entries()].map(([sid, sub]) => [sid, { since: sub.cursor }])
          ),
        }
        const token = getAuthToken()
        const headers = { 'Content-Type': 'application/json' }
        // Issue #2130 (epic #1990 stage 5c): auth token moves from getPollUrl()'s
        // query-string hack to a normal Authorization header, matching every other
        // request in this codebase (utils/api.js's apiRequest()) — not routed through
        // apiRequest() itself here because its generic catch-all rewraps a self-inflicted
        // AbortError into a generic APIError with no way to tell them apart afterward,
        // which this loop's AbortError-vs-real-failure branch depends on.
        if (token) headers['Authorization'] = `Bearer ${token}`
        const response = await fetch('/api/poll', {
          method: 'POST', headers, body: JSON.stringify(body), signal: controller.signal,
        })

        if (response.status === 401) {
          triggerReauth(401)
          if (conn.isCurrentGeneration(myGeneration)) {
            stopPolling()
          }
          return
        }

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        conn.backendStatus.value = data.backend_status || 'ok'
        if (recoveringFromError) {
          recoveringFromError = false
          retryStaleAppData()
        }

        if (data.ui) {
          await _processUIBatch(data.ui, myGeneration)
        }
        for (const [sid, sessionBatch] of Object.entries(data.sessions || {})) {
          await _processSessionBatch(sid, sessionBatch)
        }

        pushDebugEvent('polling', 'poll-cycle', {
          stream: 'multiplex', cursorAfter: uiCursor,
          sessionIds: [...subscriptions.keys()], generation: myGeneration,
        })
        // Consolidates any mid-cycle 'resyncing' transition (UI eviction/reset, or the
        // CURRENT session's own eviction/reset) back to the connected family exactly once,
        // after every stream in this response has been fully processed — simpler and more
        // robust than toggling back and forth per sub-step when multiple streams in the
        // same response could each independently need a resync.
        if (conn.isCurrentGeneration(myGeneration)) {
          conn.retryCount.value = 0
          conn.transition(connectedFamilyTarget())
        }

      } catch (err) {
        if (err.name === 'AbortError') {
          // Self-inflicted (visibility resume, or touchSubscription() interrupting a
          // stale in-flight request) or genuinely superseded — re-enter immediately, no
          // state change (nothing actually failed).
          continue
        }
        console.warn(`[poll] Connection error (retry ${conn.retryCount.value + 1}):`, err.message || err)
        pushDebugEvent('polling', 'poll-error', {
          stream: 'multiplex', message: err.message || String(err), retryCount: conn.retryCount.value + 1, generation: myGeneration
        })
        conn.retryCount.value++
        conn.transition('backoff')
        const delay = Math.min(2000 * conn.retryCount.value, 30000)
        await conn.scheduleBackoff(delay)
        if (conn.isCurrentGeneration(myGeneration)) {
          conn.transition('connecting')
          recoveringFromError = true
        }
      }
    }
  }

  // Processes the UI stream's batch. Returns true if a resync (evicted/reset) happened.
  async function _processUIBatch(uiBatch, myGeneration) {
    if (uiBatch.evicted) {
      // Issue #2065 AC7: an evicted cursor can't be trusted to deliver a gapless slice, so
      // re-fetch the UI stream's own ground truth (projects + sessions) instead of risking a
      // permanent gap by applying this response's events as a complete slice.
      pushDebugEvent('polling', 'poll-evicted', {
        stream: 'ui', staleCursor: uiCursor, next_cursor: uiBatch.next_cursor,
        eventCount: uiBatch.events?.length ?? 0, generation: myGeneration
      })
      conn.transition('resyncing')
      const sessionStore = useSessionStore()
      const idsBeforeResync = new Set(sessionStore.sessions.keys())
      await loadAppData()
      const vanishedIds = [...idsBeforeResync].filter(id => !sessionStore.sessions.has(id))
      if (vanishedIds.length > 0) {
        const wasCurrentSessionRemoved = await sessionStore.removeSessionsFromStores(vanishedIds)
        if (wasCurrentSessionRemoved) {
          router.push('/')
        }
      }
      uiCursor = uiBatch.next_cursor
      return true
    }

    if (uiBatch.events && uiBatch.events.length > 0) {
      let sawStreamReset = false
      for (const event of uiBatch.events) {
        if (event.type === 'stream_reset') {
          // Epic #1990 stage 5a (AC7): a Backend restart made explicit. Stop treating the
          // remainder of this batch as normal incremental updates — they're already-new-
          // epoch events that the resync below (triggered via dispatchEvent ->
          // UI_EVENT_HANDLERS.stream_reset) will pick up fresh; events already dispatched
          // above (before this marker, in this same batch) are legitimate pre-reset
          // history and need no unwinding.
          pushDebugEvent('polling', 'poll-reset', {
            stream: 'ui', staleCursor: uiCursor, next_cursor: uiBatch.next_cursor,
            eventCount: uiBatch.events?.length ?? 0, generation: myGeneration,
            epoch: event.data
          })
          for (const laterEvent of uiBatch.events.slice(uiBatch.events.indexOf(event) + 1)) {
            if (laterEvent.type === 'stream_reset') {
              pushDebugEvent('polling', 'poll-reset', {
                stream: 'ui', staleCursor: uiCursor, next_cursor: uiBatch.next_cursor,
                eventCount: uiBatch.events?.length ?? 0, generation: myGeneration,
                epoch: laterEvent.data, coalesced: true
              })
            }
          }
          conn.transition('resyncing')
          const sessionStore = useSessionStore()
          const idsBeforeResync = new Set(sessionStore.sessions.keys())
          await dispatchEvent(event, 'ui')
          const vanishedIds = [...idsBeforeResync].filter(id => !sessionStore.sessions.has(id))
          if (vanishedIds.length > 0) {
            const wasCurrentSessionRemoved = await sessionStore.removeSessionsFromStores(vanishedIds)
            if (wasCurrentSessionRemoved) {
              router.push('/')
            }
          }
          sawStreamReset = true
          break
        }
        await dispatchEvent(event, 'ui')
      }
      if (sawStreamReset) {
        uiCursor = uiBatch.next_cursor
        return true
      }
    }
    uiCursor = uiBatch.next_cursor
    return false
  }

  // Issue #2140: the single resync entry point for both _processSessionBatch call sites
  // below (evicted and stream_reset) AND retryResync() — claims the shared per-session
  // history-load tracker in message.js (aborting whichever attempt was previously current,
  // whether that was a prior resync or session.js's own retryHydration()) before resuming
  // via the same per-page-inactivity/abort-aware loadMessages() the initial hydration load
  // uses. Returns true if a resync was actually attempted (and succeeded); false if skipped
  // because this session's resync already hit RESYNC_FAILURE_CEILING consecutive failures
  // (terminal — only a manual retryResync() clears that). A thrown error propagates to the
  // caller, which (via the existing poll-loop catch) lands the connection in 'backoff' —
  // unchanged from before, for any attempt short of the ceiling.
  async function _resyncSession(sessionId) {
    const stall = resyncStallBySession.value.get(sessionId)
    if (stall?.terminal) return false

    const messageStore = useMessageStore()
    const controller = messageStore.claimHydrationLoad(sessionId)
    try {
      // Issue #2030: the resync fetches the newest window and merges it onto the loaded suffix
      // (contiguity / history_id rules in message.js loadHistoryTail); a gap larger than the
      // window replaces the suffix and restarts backfill.
      const result = await messageStore.loadMessages(sessionId, null, 0, {
        window: 'tail',
        resync: true,
        signal: controller.signal,
        inactivityMs: HISTORY_PAGE_INACTIVITY_TIMEOUT_MS,
      })
      pushDebugEvent('polling', 'hydration-stage', { sessionId, stage: 'resync', replaced: result?.replaced })
      // Issue #2145: background_tasks_changed events lost in the gap are not replayed.
      const backgroundSnapshot = useBackgroundTasksStore().hydrate(sessionId)
      // Issue #2030: same for task events — re-fetch the server snapshot (live ones buffer).
      const tasksSnapshot = useTaskStore().hydrate(sessionId)
      // (a gap-replace dropped the history index: this refetches it; an intact one is kept)
      import('@/composables/useHistoryBackfill').then(({ startBackfillIfIncomplete }) => startBackfillIfIncomplete(sessionId, [backgroundSnapshot, tasksSnapshot]))
      resyncAttemptsBySession.delete(sessionId)
      if (resyncStallBySession.value.has(sessionId)) {
        resyncStallBySession.value.delete(sessionId)
        resyncStallBySession.value = new Map(resyncStallBySession.value)
      }
      return true
    } catch (error) {
      const attempts = (resyncAttemptsBySession.get(sessionId) || 0) + 1
      resyncAttemptsBySession.set(sessionId, attempts)
      const terminalNow = attempts >= RESYNC_FAILURE_CEILING
      pushDebugEvent('polling', 'hydration-retry', { sessionId, attempts, terminal: terminalNow, reason: error?.message })
      resyncStallBySession.value.set(sessionId, {
        active: true,
        terminal: terminalNow,
        error: error?.message || String(error),
      })
      resyncStallBySession.value = new Map(resyncStallBySession.value)
      pushDebugEvent('polling', 'hydration-abort', { sessionId, stage: 'resync' })
      throw error
    }
  }

  // Issue #2140: manual recovery from a terminal resync stall (InputArea.vue/MessageList.vue's
  // Retry button) — resets the consecutive-failure counter/terminal flag and fires one
  // immediate attempt, adopting the fresh cursor on success exactly like the automatic paths.
  async function retryResync(sessionId) {
    const sub = subscriptions.get(sessionId)
    if (!sub) return
    resyncAttemptsBySession.delete(sessionId)
    if (resyncStallBySession.value.has(sessionId)) {
      resyncStallBySession.value.delete(sessionId)
      resyncStallBySession.value = new Map(resyncStallBySession.value)
    }
    const messageStore = useMessageStore()
    try {
      const resynced = await _resyncSession(sessionId)
      if (resynced) _adoptResyncCursor(sub, sessionId, messageStore, sub.cursor)
    } catch {
      // Stall state already recorded by _resyncSession — nothing further to do here.
    }
  }

  // Processes one subscribed session's batch — applied regardless of whether this session
  // is the CURRENT one (US2: background subscriptions are genuinely live, not just cached).
  // Only the current session's own resync surfaces as a visible `conn.transition('resyncing')`
  // — a background session's resync is a silent data-correctness detail (AC2: background
  // updates must not surface a new notification/badge beyond what already exists).
  // Returns true if a resync (evicted/reset) happened for the CURRENT session specifically.
  async function _processSessionBatch(sessionId, batch) {
    const sub = subscriptions.get(sessionId)
    if (!sub) return false // aged out locally between request and response
    const isCurrent = currentSessionId.value === sessionId

    if (batch.evicted) {
      // Issue #1988: an evicted cursor (predates the server's retained ring-buffer window)
      // can't be trusted to deliver a gapless slice — fall back to a full resync via the
      // same proven event_cursor-capturing pattern the initial session load already uses.
      pushDebugEvent('polling', 'poll-evicted', {
        stream: 'session', sessionId, staleCursor: sub.cursor, next_cursor: batch.next_cursor,
        evicted: batch.evicted,
      })
      if (isCurrent) conn.transition('resyncing', sessionId)
      const messageStore = useMessageStore()
      const resynced = await _resyncSession(sessionId)
      if (!subscriptions.has(sessionId)) return isCurrent // evicted locally during the await
      if (resynced) _adoptResyncCursor(sub, sessionId, messageStore, batch.next_cursor)
      return isCurrent
    }

    if (batch.events && batch.events.length > 0) {
      lastBatchBySession.set(sessionId, { at: Date.now(), count: batch.events.length })
      let sawStreamReset = false
      for (const event of batch.events) {
        if (event.type === 'stream_reset') {
          // Epic #1990 stage 5a (AC7): a Backend restart made explicit.
          pushDebugEvent('polling', 'poll-reset', {
            stream: 'session', sessionId, staleCursor: sub.cursor, next_cursor: batch.next_cursor,
            epoch: event.data
          })
          for (const laterEvent of batch.events.slice(batch.events.indexOf(event) + 1)) {
            if (laterEvent.type === 'stream_reset') {
              pushDebugEvent('polling', 'poll-reset', {
                stream: 'session', sessionId, staleCursor: sub.cursor, next_cursor: batch.next_cursor,
                epoch: laterEvent.data, coalesced: true
              })
            }
          }
          if (isCurrent) conn.transition('resyncing', sessionId)
          const messageStore = useMessageStore()
          const resynced = await _resyncSession(sessionId)
          if (!subscriptions.has(sessionId)) return isCurrent
          if (resynced) _adoptResyncCursor(sub, sessionId, messageStore, batch.next_cursor)
          sawStreamReset = true
          break
        }
        await dispatchEvent(event, 'session', sessionId)
      }
      if (sawStreamReset) return isCurrent
    }

    sub.cursor = batch.next_cursor
    sub.hasEverSeenResponse = true
    if (isCurrent) _syncSessionHasEverConnected()
    return false
  }

  // Issue #2030: what the last session poll batch looked like, for the history backfill
  // scheduler ("never compete with live"). Non-reactive; Map<sessionId, {at, count}>.
  const lastBatchBySession = new Map()
  function getLastBatchInfo(sessionId) {
    return lastBatchBySession.get(sessionId) || null
  }

  // Shared by both resync branches above: prefer the REST reload's own event_cursor
  // (aligned with the history it just loaded) over the poll batch's next_cursor.
  function _adoptResyncCursor(sub, sessionId, messageStore, fallbackCursor) {
    const restCursor = messageStore.loadedEventCursors.get(sessionId)
    let newCursor
    if (restCursor !== undefined) {
      messageStore.loadedEventCursors.delete(sessionId)
      newCursor = restCursor
    } else {
      newCursor = fallbackCursor
    }
    sub.cursor = newCursor
    sub.hasEverSeenResponse = true
    if (sessionId === currentSessionId.value) _syncSessionHasEverConnected()
  }

  function stopPolling() {
    conn.disconnect()
    stopAppDataRetryWatcher()
  }

  // Compat aliases
  function connectUI() { startPolling() }
  function disconnectUI() { stopPolling() }

  // Issue #2030 stage 4 PR 4d: the session left behind keeps only its newest history (memory budget).
  function _notifySessionSwitch(previousId, nextId) {
    if (previousId === nextId) return
    import('@/composables/useHistoryBackfill')
      .then(({ historyBackfill }) => historyBackfill.onSessionSwitch(previousId, nextId))
      .catch((err) => pushDebugEvent('polling', 'session-switch-hook-failed', { message: err?.message }))
  }

  async function connectSession(sessionId) {
    // Issue #1000: Prefer cursor from loadMessages() REST response (aligned with loaded
    // history). Falls back to the subscription's own cursor (resuming a previously-viewed,
    // still-subscribed session exactly where it left off — including one that's been live
    // in the background the whole time, per US2), then to a fresh API bootstrap for a
    // genuinely first-time connection.
    const messageStore = useMessageStore()
    const restCursor = messageStore.loadedEventCursors.get(sessionId)
    const existing = subscriptions.get(sessionId)
    let initialCursor
    if (restCursor !== undefined) {
      initialCursor = restCursor
      messageStore.loadedEventCursors.delete(sessionId)
    } else if (existing !== undefined) {
      initialCursor = existing.cursor
    } else {
      try {
        const result = await api.get(`/api/poll/session/${sessionId}/cursor`)
        initialCursor = result?.cursor ?? 0
      } catch {
        initialCursor = 0
      }
    }

    const previousSessionId = currentSessionId.value
    currentSessionId.value = sessionId
    isCurrentViewArchived.value = false
    _notifySessionSwitch(previousSessionId, sessionId)
    touchSubscription(sessionId)
    subscriptions.get(sessionId).cursor = initialCursor
    interruptIfStale(sessionId, initialCursor)
    _syncSessionState()
    _syncSessionHasEverConnected()
    // The shared loop is normally already running by the time any session is ever
    // selected (App.vue's initializeApp() calls startPolling() at mount, before any
    // session selection is possible) — but connectSession() must not depend on that
    // ordering: if nothing has started the loop yet, start it now, fire-and-forget,
    // exactly like the pre-consolidation design's own connectSession() always
    // guaranteed a live, polling connection on its own.
    if (conn.state.value === 'idle') {
      startPolling(uiCursor)
    }
  }

  async function disconnectSession() {
    const sessionId = currentSessionId.value
    // Full eviction, not just "stop showing it as current" — every existing call site
    // (mark-unread race prevention, restart/reset-then-reconnect, end session) wants a
    // clean slate, not "keep polling it quietly in the background." Normal navigation
    // away from a session (switching to view another one) never calls this at all —
    // connectSession() for the NEW session is all that runs, leaving the old one's
    // subscription live in the LRU exactly as US2 intends.
    if (sessionId) evictSubscription(sessionId)
    currentSessionId.value = null
    isCurrentViewArchived.value = false
    _syncSessionState()
    _syncSessionHasEverConnected()
  }

  // Epic #1990 stage 5c: clears a session's subscription entirely (cursor + LRU membership),
  // forcing its NEXT connectSession() to start fresh (bootstrap cursor) instead of resuming an
  // old position. Used both for a single current session being reset/restarted by the user
  // (in practice always redundant with disconnectSession() already having evicted it, every
  // such call site calls both) and for a permanently-deleted session's teardown (session.js's
  // deleteSession()/removeSessionsFromStores(), which can call this for many sessions at once,
  // most of which are NOT the current session). Issue #2160 AC6: previously two identical
  // one-line evictSubscription(sessionId) wrappers (resetSessionCursor/
  // cleanupSessionPollingState) under different names for these two call patterns —
  // consolidated into this one export since neither had any behavior of its own beyond the
  // call to evictSubscription().
  function resetSessionCursor(sessionId) {
    evictSubscription(sessionId)
  }

  // Epic #1990 stage 5b (AC3/UC3, decision 5): an archived/ephemeral-with-no-archives
  // session view never starts a poll loop at all. Deliberately does NOT touch `subscriptions`
  // — an archived snapshot view must neither subscribe a session that isn't already live, nor
  // interrupt one that happens to share the same sessionId and IS genuinely still live
  // elsewhere (e.g. open in another tab/background). `isCurrentViewArchived` is a plain,
  // independently-owned ref (not derived from `conn`'s transport state), so unlike stage 5b's
  // per-connection 'archived' state, no stale in-flight poll response can ever revert it —
  // nothing else ever writes it.
  function markSessionArchived(sessionId) {
    const previousSessionId = currentSessionId.value
    currentSessionId.value = sessionId
    _notifySessionSwitch(previousSessionId, sessionId)
    isCurrentViewArchived.value = true
    _syncSessionState()
    _syncSessionHasEverConnected()
  }

  // ========== PAGE VISIBILITY ==========
  function setupVisibilityHandler() {
    if (visibilityUnsubscribe) visibilityUnsubscribe()

    const handler = () => {
      if (document.visibilityState === 'visible') {
        pushDebugEvent('polling', 'visibility-reconnect', { sessionId: currentSessionId.value })
        conn.abortInFlight()
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

  // ========== MESSAGE HANDLERS ==========
  // Each handler: (payload, sessionId) => void. sessionId is only meaningful for
  // SESSION_EVENT_HANDLERS (now any member of the subscription set, enforced by
  // dispatchEvent below — no longer only the current session, epic #1990 stage 5c AC1);
  // UI_EVENT_HANDLERS entries read whatever session id they need from payload.data.
  const UI_EVENT_HANDLERS = {
    state_change: (payload) => {
      const sessionStore = useSessionStore()
      if (payload.data && payload.data.session_id && payload.data.session) {
        const priorSession = sessionStore.sessions.get(payload.data.session_id)
        const priorState = priorSession?.state

        sessionStore.updateSession(payload.data.session_id, payload.data.session)

        const changedSessionId = payload.data.session_id
        const newState = payload.data.session.state

        // Issue #2145: background tasks die with the process — real teardown only
        // (paused/awaiting-permission keeps them running).
        if (newState === 'error' || newState === 'terminated') {
          useBackgroundTasksStore().clear(changedSessionId)
        }

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
          useBackgroundTasksStore().hydrate(changedSessionId)
          useTaskStore().hydrate(changedSessionId)
          messageStore.hydrateBackgroundAgents(changedSessionId).then(async () => {
            await messageStore.loadMessages(changedSessionId, null, 0, { window: 'tail' })
            const { startBackfillIfIncomplete } = await import('@/composables/useHistoryBackfill')
            startBackfillIfIncomplete(changedSessionId)  // also schedules the (dropped) history index
          })
        }

        if (newState === 'active') {
          useMcpStore().fetchMcpStatus(changedSessionId)
        }

        if (newState === 'error' && priorState !== 'error') {
          notify('session_error', { sessionName: payload.data.session.name || 'Session', sessionId: changedSessionId })
        }
        if (newState === 'paused' && priorState !== 'paused') {
          notify('permission_prompt', { sessionName: payload.data.session.name || 'Session', sessionId: changedSessionId })
        }
      }
    },

    // Issue #2159: metadata-only session updates (name, model, permission_mode,
    // allowed_tools, order, unread tracking, etc.) — split out of state_change
    // since the SessionState itself never changes for these. Same local-apply as
    // state_change's own session merge, minus the state-transition side effects
    // (error reload, mcp refetch, notifications) that don't apply here.
    session_metadata_updated: (payload) => {
      if (payload.data && payload.data.session_id && payload.data.session) {
        useSessionStore().updateSession(payload.data.session_id, payload.data.session)
      }
    },

    // Issue #2184: is_processing-only session updates — split out of state_change
    // the same way session_metadata_updated was (SessionState itself never changes
    // for a turn starting/ending). The task_complete notify trigger moves here from
    // state_change's handler: most turn completions never change SessionState, so
    // that notification would otherwise silently stop firing for the common case
    // once the backend stops emitting state_change for processing-only changes.
    processing_change: (payload) => {
      if (payload.data && payload.data.session_id && payload.data.session) {
        const sessionStore = useSessionStore()
        const priorSession = sessionStore.sessions.get(payload.data.session_id)
        const wasProcessing = priorSession?.is_processing

        sessionStore.updateSession(payload.data.session_id, payload.data.session)

        if (wasProcessing && !payload.data.session.is_processing) {
          notify('task_complete', {
            sessionName: payload.data.session.name || 'Session',
            sessionId: payload.data.session_id,
          })
        }
      }
    },

    session_reset: (payload) => {
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
        const sessionStore = useSessionStore()
        sessionStore.recordSessionReset(resetSessionId)
        // Epic #1990 stage 5c: clears the subscription cursor entirely (not just a cache
        // entry) — safe to run even if a resync is concurrently in flight for this exact
        // session (the "reset arriving during a history load" race): the in-flight resync's
        // own cursor adoption (_adoptResyncCursor above) re-checks subscriptions.has()
        // after its own await and simply no-ops if this ran first.
        evictSubscription(resetSessionId)
      }
    },

    project_updated: (payload) => {
      const sessionStore = useSessionStore()
      const projectStore = useProjectStore()
      if (payload.data && payload.data.project) {
        const project = payload.data.project
        projectStore.updateProjectLocal(project.project_id, project)
        sessionStore.fetchSessions()
      }
    },

    project_deleted: (payload) => {
      const projectStore = useProjectStore()
      if (payload.data && payload.data.project_id) {
        projectStore.projects.delete(payload.data.project_id)
      }
    },

    session_deleted: (payload) => {
      const sessionStore = useSessionStore()
      if (payload.data?.session_id) {
        sessionStore.removeSessionsFromStores([payload.data.session_id]).then((wasCurrentSessionRemoved) => {
          if (wasCurrentSessionRemoved) {
            router.push('/')
          }
        })
      }
    },

    notification: (payload) => {
      if (payload.data?.event_type === 'minion_comm') {
        notify('minion_comm', {
          commType: payload.data.comm_type,
          fromMinion: payload.data.from_minion_name || 'Minion',
          sessionId: payload.data.session_id
        })
      }
    },

    mcp_oauth_complete: (payload) => {
      const serverId = payload.data?.server_id
      if (serverId) {
        const mcpConfigStore = useMcpConfigStore()
        mcpConfigStore.fetchOAuthStatus(serverId)
        // Issue #1387: complete any pending Reconnect flow for this server
        if (mcpConfigStore.pendingReconnect.get(serverId)) {
          mcpConfigStore.completeReconnect(serverId).then(() => {
            useSecretsStore().fetchSecrets()
          }).catch(e => console.error('[Reconnect] import-as-secret failed:', e))
        }
      }
    },

    mcp_oauth_refreshed: (payload) => {
      // Issue #976: Background refresh succeeded — update status indicator
      const serverId = payload.data?.server_id
      if (serverId) {
        useMcpConfigStore().fetchOAuthStatus(serverId)
      }
    },

    secret_refreshed: (payload) => {
      // Issue #1387: VaultRefreshManager background refresh succeeded
      const secretName = payload.data?.secret_name
      if (secretName) {
        useSecretsStore().handleSecretRefreshed(secretName)
      }
    },

    secret_refresh_failed: (payload) => {
      // Issue #1387: VaultRefreshManager background refresh permanently failed
      const secretName = payload.data?.secret_name
      if (secretName) {
        useSecretsStore().handleSecretRefreshFailed(secretName, payload.data?.error || '')
      }
    },

    secret_oauth_complete: (payload) => {
      // Issue #1871: standalone vault-secret guided-authorization flow finished
      // (success or failure) — mirrors mcp_oauth_complete, but fires on failure
      // too since the settings panel has no other way to learn the outcome of a
      // flow completed in a cross-origin popup.
      if (payload.data?.flow_id) {
        useSecretsStore().handleSecretOAuthComplete(payload.data)
      }
    },

    schedule_updated: (payload) => {
      const scheduleStore = useScheduleStore()
      scheduleStore.handleScheduleEvent(payload.data?.legion_id, payload.data || {})
    },

    schedule_execution: (payload) => {
      const scheduleStore = useScheduleStore()
      scheduleStore.handleScheduleExecution(payload.data?.legion_id, payload.data || {})
    },

    schedule_monitor_error: (payload) => {
      const scheduleStore = useScheduleStore()
      scheduleStore.handleScheduleMonitorError(
        payload.data?.legion_id, payload.data || {}
      )
    },

    session_restart_error: (payload) => {
      console.error(
        `[session_restart_error] Session ${payload.data?.session_id}: ${payload.data?.error}`
      )
      notify('session_restart_error', {
        sessionId: payload.data?.session_id,
        error: payload.data?.error,
      })
    },

    server_restarting: (payload) => {
      const uiStore = useUIStore()
      // The initiating tab's own doRestart() already drives phase='progress' locally — it
      // also receives this exact broadcast on its own UI poll (global ui_queue, every tab
      // reads it), and must not have its own in-progress modal reset out from under it.
      if (uiStore.restartInProgress) return
      uiStore.showRestartModal({ remote: true, message: payload.data?.message })
    },

    session_self_restart: (payload) => {
      const { session_id: sessionId, reason } = payload.data || {}
      console.log(`[session_self_restart] Session ${sessionId} restarted successfully${reason ? ` (reason: ${reason})` : ''}`)
      notify('session_self_restart', { sessionId, reason })
    },

    rate_limits_update: (payload) => {
      const uiStore = useUIStore()
      uiStore.setRateLimits(payload.data)
    },

    session_watchdog_alert: (payload) => {
      const uiStore = useUIStore()
      uiStore.pushAlert(payload.data || {})
      notify('session_error', { sessionName: payload.data?.session_name || 'Session', sessionId: payload.data?.session_id })
    },

    // Epic #1990 stage 5a (AC7): a Backend restart, made explicit. The caller
    // (the poll loop above) is responsible for the surrounding bookkeeping
    // (vanished-session cleanup, cursor adoption, stopping further per-event
    // processing of that batch) — this handler is only the data-level resync
    // itself.
    stream_reset: async () => {
      await loadAppData()
    },
  }

  const SESSION_EVENT_HANDLERS = {
    message: (payload, sessionId) => {
      const message = payload.data
      if (!message || !message.type) {
        console.warn('Received message event with invalid data:', payload)
        return
      }
      const messageStore = useMessageStore()
      const sessionStore = useSessionStore()
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
      messageStore.applyRecord(sessionId, message, 'live')
    },

    tool_call: (payload, sessionId) => {
      useMessageStore().applyRecord(sessionId, payload.data || payload, 'live')
    },

    resource_registered: (payload, sessionId) => {
      if (payload.data?.resource) {
        const resourceStore = useResourceStore()
        resourceStore.addResource(sessionId, payload.data.resource)
      }
    },

    link_registered: (payload, sessionId) => {
      if (payload.data?.link) {
        useLinksStore().addLink(sessionId, payload.data.link)
      }
    },

    resource_removed: (payload, sessionId) => {
      if (payload.data?.resource_id) {
        const resourceStore = useResourceStore()
        resourceStore.handleResourceRemoved(sessionId, payload.data.resource_id)
      }
    },

    queue_update: (payload, sessionId) => {
      const queueStore = useQueueStore()
      queueStore.handleQueueUpdate(sessionId, payload.data || {})
    },

    usage_updated: (payload) => {
      useUsageStore().handleUsageUpdated(payload.data || {})
    },

    context_update: (payload, sessionId) => {
      const sessionStore = useSessionStore()
      const { input_tokens, context_window, context_pct } = payload.data || {}
      sessionStore.patchSession(sessionId, {
        context_input_tokens: input_tokens,
        context_window: context_window,
        context_pct: context_pct,
      })
    },

    // Issue #1486: streaming text delta — forward to message store for RAF-batched mutation
    assistant_delta: (payload, sessionId) => {
      useMessageStore().handleAssistantDelta(sessionId, payload.data)
    },

    // Issue #2132: thinking_tokens progress deltas are live-only, never persisted/rendered.
    // No UI surface exists for this yet (same registered-but-unconsumed acknowledgment
    // pattern as proxy_event below) — just stops it from vanishing into the "unknown type"
    // warning instead of building a progress indicator nothing asked for.
    thinking_tokens_update: () => {},

    secret_refresh_failed: (payload) => {
      // backend/routers/secrets.py's proxy-sidecar event route — same fact as the UI-stream
      // secret_refresh_failed handler above, just nested under `data` instead of flat, and
      // arriving via a different transport (Docker proxy addon -> session queue, instead of
      // VaultRefreshManager -> UI queue). Routes to the identical, already-silent treatment
      // (useSecretsStore().handleSecretRefreshFailed has no toast/notify even on the UI-stream
      // path) so there's exactly one visible outcome for this fact regardless of transport.
      const secretName = payload.data?.secret_name
      if (secretName) {
        useSecretsStore().handleSecretRefreshFailed(secretName, payload.data?.error || '')
      }
    },

    proxy_event: (payload) => {
      // Registered catch-all default (backend/routers/secrets.py's event_type = body.get(
      // "type", "proxy_event")) — confirmed nothing in the live codebase currently emits it
      // (the one real caller, backend/docker/proxy/addon.py's _emit_ui_event, always passes an
      // explicit real type). No UI surface exists for this yet because nothing produces it;
      // this just acknowledges the registered type instead of silently warning/dropping it, so
      // a future real producer surfaces in the console instead of vanishing outright.
      console.log('[proxy_event]', payload.data)
    },

    // Epic #1990 stage 5a (AC7): a Backend restart, made explicit. Registered
    // here so registeredEventTypes.session covers it (completeness test), but
    // the poll loop above never actually reaches this entry via dispatchEvent()
    // — it intercepts stream_reset itself and calls messageStore.loadMessages()
    // directly. Kept as a correct, self-consistent fallback in case anything
    // else ever dispatches this type through the normal path.
    stream_reset: async (payload, sessionId) => {
      await _resyncSession(sessionId)
    },
  }

  function dispatchEvent(payload, stream, sessionId) {
    if (stream === 'session') {
      // Epic #1990 stage 5c (AC1): the current-session-only guard becomes a
      // subscription-SET membership guard — any subscribed session (current or
      // a recently-viewed background one) gets its events applied live, not just
      // the one currently open. UI-stream events are never guarded — unchanged
      // from before: they carry their own target session id in their payload and
      // can legitimately apply to a background session.
      if (!subscriptions.has(sessionId)) return
    }
    const handlers = stream === 'ui' ? UI_EVENT_HANDLERS : SESSION_EVENT_HANDLERS
    // Object.hasOwn guard: a plain object literal otherwise inherits Object.prototype, so a
    // payload.type of e.g. "constructor"/"toString" would resolve to an inherited function
    // and get invoked instead of hitting the unknown-type warning below.
    const handler = Object.hasOwn(handlers, payload.type) ? handlers[payload.type] : undefined
    if (!handler) {
      console.warn(`Unknown ${stream} poll message type:`, payload.type)
      return
    }
    // Returned (not just invoked) so a caller that needs to wait for an async
    // handler — currently only stream_reset's resync — can await it.
    return handler(payload, sessionId)
  }

  // ========== RETURN ==========
  return {
    // Epic #1990 stage 5c: one shared multiplexed connection now backs both of these —
    // uiState/sessionState (and the backend-status/retry-count pairs below) are exposed
    // separately only for API-shape compatibility with existing call sites (InputArea.vue,
    // HeaderRow1.vue); their values agree by construction except sessionState's archived
    // override, which `conn` itself can never produce or revert on its own.
    uiState: conn.state,
    uiRetryCount: conn.retryCount,
    uiBackendStatus: conn.backendStatus,
    sessionState: sessionStateRef,
    sessionRetryCount: conn.retryCount,
    sessionBackendStatus: conn.backendStatus,
    // Per-session now (epic #1990 stage 5c) rather than per-connection: true once THIS
    // session's subscription has received at least one successful poll response since being
    // subscribed — including one that's been live in the background the whole time, so
    // switching to an already-subscribed session shows input enabled immediately rather than
    // waiting out a fresh connect cycle.
    sessionHasEverConnected: sessionHasEverConnectedRef,
    // Issue #2161 (AC4): per-CONNECTION (not per-session) signal — true once this page's
    // transport has reached 'connected' at least once since its last connect()/
    // disconnect() cycle. Lets InputArea.vue distinguish a genuine first connection
    // ("Connecting...") from a reconnect after a previously-successful connection was
    // lost ("Reconnecting..."), unlike sessionHasEverConnected above which is scoped to
    // one session's own subscription.
    transportHasEverConnected: conn.hasEverConnected,
    currentSessionId,
    legionConnected,
    legionRetryCount,
    currentLegionId,
    registeredEventTypes: {
      ui: Object.keys(UI_EVENT_HANDLERS),
      session: Object.keys(SESSION_EVENT_HANDLERS),
    },

    connectUI,
    disconnectUI,
    loadAppData,
    startUIPolling: startPolling,
    stopUIPolling: stopPolling,
    setupVisibilityHandler,
    connectSession,
    disconnectSession,
    resetSessionCursor,
    markSessionArchived,
    getLastBatchInfo,
    sendMessage,
    sendPermissionResponse,
    sendPermissionResponseWithInput,
    interruptSession,

    // Issue #2140: resync stall visibility (AC5) — a separate map from session.js's
    // hydrationStageBySession since a resync can happen to an already-ready session.
    resyncStallBySession,
    retryResync,
  }
})
