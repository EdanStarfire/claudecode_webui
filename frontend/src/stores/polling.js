import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session'
import { useProjectStore } from './project'
import { useMessageStore } from './message'
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

// ========== STREAM CONNECTION STATE MACHINE (Epic #1990 stage 5b) ==========
// Single state machine per long-poll stream (ui, session). Liveness is derived ONLY from
// poll responses and fetch outcomes — a successful response (including an empty one after
// the server's long-poll hold) proves the connection is alive, a thrown fetch error proves
// it isn't. No wall-clock staleness timer exists anywhere in this design.
//
// States: idle | connecting | connected | resyncing | backoff | unreachable | archived
// `transition()` is the single function that ever assigns `.state` — every poll-loop branch
// calls it, nothing else writes state directly. This is both the one-reducer requirement and
// the single instrumentation point (every transition emits a 'state-transition' debug event).
function createStreamConnection(kind) {
  const state = ref('idle')
  // 'ok' | 'degraded' | 'unreachable' — kept as its OWN field, not collapsed into `state`, so
  // the header can still distinguish "degraded" (needs manual restart) from "unreachable"
  // (self-heals) exactly as the pre-5b STATE_SEVERITY ladder did.
  const backendStatus = ref('ok')
  const retryCount = ref(0)
  // True once this connection has reached 'connected' at least once since its last connect()/
  // disconnect() cycle — InputArea's gating boundary (decision 4) keys off this rather than
  // the live transport state, so a backoff/resync cycle after a prior success doesn't disable
  // input the way a genuine never-yet-connected session does.
  const hasEverConnected = ref(false)

  let cursor = 0
  let abortController = null
  let generation = 0
  let backoffTimerHandle = null
  let backoffResolve = null

  function transition(next, sessionId) {
    const from = state.value
    state.value = next
    if (next === 'connected') hasEverConnected.value = true
    pushDebugEvent('polling', 'state-transition', { kind, sessionId: sessionId ?? null, from, to: next })
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

  function connect(initialCursor, sessionId) {
    cursor = initialCursor
    retryCount.value = 0
    hasEverConnected.value = false
    const gen = bumpGeneration()
    transition('connecting', sessionId)
    return gen
  }

  function markArchived(sessionId) {
    transition('archived', sessionId)
  }

  // Synchronous teardown: abort any in-flight fetch, clear any pending backoff sleep, bump
  // the generation, and go idle. No awaited exit-budget needed — a since-superseded loop's
  // own in-flight `await fetch(...)`/`await resync()` simply finds its generation stale on
  // resume and returns without mutating anything (the loop's own generation check already
  // guarantees exit).
  function disconnect(sessionId) {
    clearBackoffTimer()
    abortController?.abort()
    abortController = null
    bumpGeneration()
    retryCount.value = 0
    hasEverConnected.value = false
    // Builder-review finding: with backendStatus now tracked per-StreamConnection (decision
    // 1) rather than shared, a value left over from the connection this is tearing down
    // would otherwise freeze and leak into whatever uses this connection next (a different
    // session, or an archived/no-poll view) with nothing left to ever refresh it.
    backendStatus.value = 'ok'
    transition('idle', sessionId)
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
    getCursor: () => cursor,
    setCursor: (c) => { cursor = c },
    newAbortController,
    abortInFlight,
    transition,
    connect,
    markArchived,
    disconnect,
    bumpGeneration,
    isCurrentGeneration,
    scheduleBackoff,
  }
}

export const usePollingStore = defineStore('polling', () => {
  // ========== STATE ==========
  const uiConn = createStreamConnection('ui')
  const sessionConn = createStreamConnection('session')

  // The session the session connection is currently targeting (mirrors the pre-5b field of
  // the same name) — null when no session has ever been selected/archived-viewed in this tab.
  const currentSessionId = ref(null)
  const currentLegionId = ref(null)  // stub - always null
  const legionConnected = ref(false)  // stub - always false
  const legionRetryCount = ref(0)    // stub

  // Poll cursors
  let uiCursor = 0
  // Issue #1974/#2129 stage 5b: cross-session cursor CACHE — not heuristic bookkeeping, this
  // is load-bearing stream-position state. Switching A -> B -> back to A, with A's history
  // already cached in message.js (so loadMessages() is skipped on switch-back), must resume
  // A's poll from exactly where it left off rather than bootstrapping "cursor right now" and
  // silently skipping whatever arrived for A while the user was viewing B. Kept as a small
  // standalone Map, separate from sessionConn's own private live-cursor field (which only ever
  // tracks ONE session at a time — the one currently being actively polled).
  const sessionCursors = {}

  // Page Visibility cleanup
  let visibilityUnsubscribe = null

  // ========== HELPERS ==========
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

  // Shared by both poll loops: compute the "poll succeeded, not mid-resync" target state
  // from the response's own backend_status. Per the transition table: backend_status ===
  // 'unreachable' always wins (AC2 — "backend_status decides the unreachable state"
  // literally), otherwise plain connected. `degraded` deliberately does NOT divert from
  // this — it's an independent, non-overriding annotation carried on backendStatus alone
  // (decision 1). The resyncing->connected transition (once a resync itself resolves) also
  // goes through this same function, since it's the identical backend_status check — the
  // caller is just never mid-batch-processing at that point, so there's no separate
  // "sawResetOrEvicted" case to express here (that transition is made directly via
  // `transition('resyncing', ...)` at the point the marker is observed).
  function connectedFamilyTarget(conn) {
    return conn.backendStatus.value === 'unreachable' ? 'unreachable' : 'connected'
  }

  // ========== UI POLL LOOP ==========
  async function startUIPolling(initialCursor = 0) {
    if (uiConn.state.value !== 'idle') return
    uiCursor = initialCursor
    startAppDataRetryWatcher()
    const myGeneration = uiConn.connect(uiCursor)
    // Issue #1977: tracks "we backed off after an error and are waiting to confirm
    // we're actually back" — retryStaleAppData() fires once, on the next CONFIRMED
    // successful response, not merely once the backoff delay elapses. Firing on backoff
    // alone would re-fire on every retry tick during a sustained total outage (each
    // subsequent fetch attempt still failing), roughly doubling REST traffic against a
    // server that's already known unreachable.
    let recoveringFromError = false

    while (uiConn.state.value !== 'idle') {
      try {
        const controller = uiConn.newAbortController()
        const url = getPollUrl('/api/poll/ui', uiCursor)
        const response = await fetch(url, { signal: controller.signal })

        if (response.status === 401) {
          triggerReauth(401)
          // Builder-review finding: a since-superseded generation's in-flight response
          // (e.g. this loop was stopped and a new one started while this fetch was still
          // pending) must not tear down the NEWER, actually-current generation's
          // connection. stopUIPolling() (not a bare flag flip) so stopAppDataRetryWatcher()
          // actually runs — otherwise appDataRetryInterval keeps firing every 15s with
          // nothing left to stop it until a full page reload.
          if (uiConn.isCurrentGeneration(myGeneration)) {
            stopUIPolling()
          }
          return
        }

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        uiConn.backendStatus.value = data.backend_status || 'ok'
        if (recoveringFromError) {
          recoveringFromError = false
          // Issue #1977 (AC3, AC7): a confirmed successful poll after an outage means
          // the connection is genuinely back — retry anything that didn't survive that
          // outage immediately, rather than waiting for the periodic watcher's next tick.
          retryStaleAppData()
        }

        if (data.evicted) {
          // Issue #2065 AC7: an evicted cursor can't be trusted to deliver a gapless slice, so
          // re-fetch the UI stream's own ground truth (projects + sessions) instead of risking a
          // permanent gap by applying this response's events as a complete slice. No
          // isCurrentGeneration-style guard needed here, unlike the session loop's version — the
          // UI loop has exactly one poll generation at a time, with no stall-heal-reconnect flow
          // creating an overlapping second one.
          pushDebugEvent('polling', 'poll-evicted', {
            stream: 'ui', staleCursor: uiCursor, next_cursor: data.next_cursor,
            eventCount: data.events?.length ?? 0, generation: myGeneration
          })
          uiConn.transition('resyncing')
          // Review finding: this resync replaces the dropped events entirely, including any
          // session_deleted this cycle would otherwise have carried — loadAppData()'s own
          // fetchSessions() only removes a vanished id from the `sessions` Map (it has no
          // reason to know about the other per-session stores), so without this, a deletion
          // that happens to land in the same evicted cycle would leak exactly the state
          // AC6 added removeSessionsFromStores() to clean up. Diff the session id set
          // before/after the resync and run the same cleanup any other session_deleted goes
          // through for whatever vanished.
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
          uiCursor = data.next_cursor
          uiConn.setCursor(uiCursor)
          uiConn.retryCount.value = 0
          uiConn.transition(connectedFamilyTarget(uiConn))
          continue
        }

        if (data.events && data.events.length > 0) {
          let sawStreamReset = false
          for (const event of data.events) {
            if (event.type === 'stream_reset') {
              // Epic #1990 stage 5a (AC7): a Backend restart made explicit. Stop
              // treating the remainder of this batch as normal incremental
              // updates — they're already-new-epoch events that the resync
              // below (triggered via dispatchEvent -> UI_EVENT_HANDLERS.
              // stream_reset) will pick up fresh; events already dispatched
              // above (before this marker, in this same batch) are legitimate
              // pre-reset history and need no unwinding.
              pushDebugEvent('polling', 'poll-reset', {
                stream: 'ui', staleCursor: uiCursor, next_cursor: data.next_cursor,
                eventCount: data.events?.length ?? 0, generation: myGeneration,
                epoch: event.data
              })
              // A rare but reachable burst (e.g. a backgrounded tab across two
              // Backend restarts before its next poll) can land more than one
              // marker in this same batch — log each so the debug trace doesn't
              // undercount how many restarts actually happened, even though one
              // resync below already covers all of them (it pulls live ground
              // truth, a superset regardless of restart count).
              for (const laterEvent of data.events.slice(data.events.indexOf(event) + 1)) {
                if (laterEvent.type === 'stream_reset') {
                  pushDebugEvent('polling', 'poll-reset', {
                    stream: 'ui', staleCursor: uiCursor, next_cursor: data.next_cursor,
                    eventCount: data.events?.length ?? 0, generation: myGeneration,
                    epoch: laterEvent.data, coalesced: true
                  })
                }
              }
              uiConn.transition('resyncing')
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
            uiCursor = data.next_cursor
            uiConn.setCursor(uiCursor)
            uiConn.retryCount.value = 0
            uiConn.transition(connectedFamilyTarget(uiConn))
            continue
          }
        }
        pushDebugEvent('polling', 'poll-cycle', {
          stream: 'ui', cursorBefore: uiCursor, cursorAfter: data.next_cursor, generation: myGeneration
        })
        uiCursor = data.next_cursor
        uiConn.setCursor(uiCursor)
        uiConn.retryCount.value = 0
        uiConn.transition(connectedFamilyTarget(uiConn))

      } catch (err) {
        if (err.name === 'AbortError') {
          // Visibility resume or stop — re-enter immediately if still polling, no state
          // change (nothing actually failed).
          continue
        }
        console.warn(`[UI poll] Connection error (retry ${uiConn.retryCount.value + 1}):`, err.message || err)
        pushDebugEvent('polling', 'poll-error', {
          stream: 'ui', message: err.message || String(err), retryCount: uiConn.retryCount.value + 1, generation: myGeneration
        })
        uiConn.retryCount.value++
        uiConn.transition('backoff')
        const delay = Math.min(2000 * uiConn.retryCount.value, 30000)
        await uiConn.scheduleBackoff(delay)
        if (uiConn.isCurrentGeneration(myGeneration)) {
          uiConn.transition('connecting')
          recoveringFromError = true
        }
      }
    }
  }

  function stopUIPolling() {
    uiConn.disconnect()
    stopAppDataRetryWatcher()
  }

  // Compat aliases
  function connectUI() { startUIPolling() }
  function disconnectUI() { stopUIPolling() }

  // ========== SESSION POLL LOOP ==========
  async function _runSessionPollLoop(sessionId, myGeneration) {
    while (sessionConn.isCurrentGeneration(myGeneration)
           && sessionConn.state.value !== 'idle'
           && currentSessionId.value === sessionId) {
      try {
        const controller = sessionConn.newAbortController()
        const cursor = sessionConn.getCursor()
        const url = getPollUrl(`/api/poll/session/${sessionId}`, cursor)
        const response = await fetch(url, { signal: controller.signal })

        if (response.status === 401) {
          triggerReauth(401)
          // Builder-review finding: a since-superseded generation's response must not write
          // the shared sessionConn singleton's state — the same guard every other write path
          // in this loop already applies. `break` runs regardless since exiting a stale
          // loop is always correct; only the state write itself needs the guard.
          if (sessionConn.isCurrentGeneration(myGeneration) && currentSessionId.value === sessionId) {
            sessionConn.transition('idle', sessionId)
          }
          break
        }

        if (response.status === 404) {
          // Session not found - stop polling
          if (sessionConn.isCurrentGeneration(myGeneration) && currentSessionId.value === sessionId) {
            sessionConn.transition('idle', sessionId)
          }
          break
        }

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        // Issue #1795: any successful response — even one carrying zero events after the
        // server's long-poll hold — proves this connection is alive. But only for the
        // CURRENT generation/session: an in-flight response can still land after the
        // generation was bumped (e.g. a session switch racing this same response, still
        // mid `await response.json()` when the abort fired too late to matter). A response
        // from a since-superseded generation must not write backendStatus or advance the
        // cursor cache — doing so would clobber state a newer generation already owns.
        const isCurrentGeneration = sessionConn.isCurrentGeneration(myGeneration) && currentSessionId.value === sessionId
        if (isCurrentGeneration) {
          sessionConn.backendStatus.value = data.backend_status || 'ok'
        }

        if (data.evicted) {
          // Issue #1988: an evicted cursor (predates the server's retained ring-buffer
          // window) can't be trusted to deliver a gapless slice — fall back to a full
          // resync via the same proven event_cursor-capturing pattern the initial
          // session load already uses (loadMessages()), instead of risking a
          // permanent gap by applying this response's events as if they were a
          // complete slice.
          pushDebugEvent('polling', 'poll-evicted', {
            stream: 'session', sessionId, staleCursor: cursor, next_cursor: data.next_cursor,
            evicted: data.evicted, generation: myGeneration
          })
          if (isCurrentGeneration) {
            sessionConn.transition('resyncing', sessionId)
            const messageStore = useMessageStore()
            // Deliberately not wrapped in a local try/catch: a failure here is a
            // connection-level problem exactly like any other poll failure, so it
            // should go through the SAME backoff/retry-count path as the outer
            // catch below rather than a second, weaker, unthrottled one — swallowing
            // it locally and retrying immediately would let a persistent failure
            // hammer the history endpoint every poll cycle with no backoff, which is
            // the same "wasted round trips" pattern this issue exists to eliminate.
            await messageStore.loadMessages(sessionId)
            // Re-check the guard AFTER the await: a concurrent session switch can
            // supersede this loop's generation while the reload is in flight. Adopting
            // this reload's cursor onto a since-superseded generation would clobber the
            // freshly-reconnected generation's own correct cursor with a stale one.
            if (sessionConn.isCurrentGeneration(myGeneration) && currentSessionId.value === sessionId) {
              const restCursor = messageStore.loadedEventCursors.get(sessionId)
              let newCursor
              if (restCursor !== undefined) {
                messageStore.loadedEventCursors.delete(sessionId)
                newCursor = restCursor
              } else {
                newCursor = data.next_cursor
              }
              sessionConn.setCursor(newCursor)
              sessionCursors[sessionId] = newCursor
              sessionConn.retryCount.value = 0
              sessionConn.transition(connectedFamilyTarget(sessionConn), sessionId)
            }
          } else {
            sessionCursors[sessionId] = data.next_cursor
          }
          continue
        }

        if (data.events && data.events.length > 0) {
          let sawStreamReset = false
          for (const event of data.events) {
            if (event.type === 'stream_reset') {
              // Epic #1990 stage 5a (AC7): a Backend restart made explicit — the
              // restart-during-stall case issue #1988's evicted/reset split used to
              // cover (reset=true, evicted deliberately false) now arrives this way
              // instead of as a top-level flag. Stop treating the remainder of this
              // batch as normal incremental updates — they're already-new-epoch
              // events the resync below will pick up fresh; events already
              // dispatched above (before this marker, in this same batch) are
              // legitimate pre-reset history and need no unwinding.
              pushDebugEvent('polling', 'poll-reset', {
                stream: 'session', sessionId, staleCursor: cursor, next_cursor: data.next_cursor,
                generation: myGeneration, epoch: event.data
              })
              // See the UI loop's identical comment above: a rare but reachable
              // burst can land more than one marker in this same batch — log
              // each so the debug trace doesn't undercount restarts, even though
              // one resync below already covers all of them.
              for (const laterEvent of data.events.slice(data.events.indexOf(event) + 1)) {
                if (laterEvent.type === 'stream_reset') {
                  pushDebugEvent('polling', 'poll-reset', {
                    stream: 'session', sessionId, staleCursor: cursor, next_cursor: data.next_cursor,
                    generation: myGeneration, epoch: laterEvent.data, coalesced: true
                  })
                }
              }
              if (isCurrentGeneration) {
                sessionConn.transition('resyncing', sessionId)
                const messageStore = useMessageStore()
                // Calls loadMessages() directly rather than through dispatchEvent():
                // dispatchEvent()'s own currentSessionId guard reads the session
                // store's value, while isCurrentGeneration above is the polling
                // store's own tracked value — the two can legitimately diverge
                // (e.g. mid-switch), and isCurrentGeneration is already the
                // authoritative guard for this resync; routing through a second,
                // differently-sourced guard risked silently skipping the reload.
                // Same not-locally-caught rationale as the evicted branch above.
                await messageStore.loadMessages(sessionId)
                if (sessionConn.isCurrentGeneration(myGeneration) && currentSessionId.value === sessionId) {
                  const restCursor = messageStore.loadedEventCursors.get(sessionId)
                  let newCursor
                  if (restCursor !== undefined) {
                    messageStore.loadedEventCursors.delete(sessionId)
                    newCursor = restCursor
                  } else {
                    newCursor = data.next_cursor
                  }
                  sessionConn.setCursor(newCursor)
                  sessionCursors[sessionId] = newCursor
                  sessionConn.retryCount.value = 0
                  sessionConn.transition(connectedFamilyTarget(sessionConn), sessionId)
                }
              } else {
                sessionCursors[sessionId] = data.next_cursor
              }
              sawStreamReset = true
              break
            }
            await dispatchEvent(event, 'session', sessionId)
          }
          if (sawStreamReset) {
            continue
          }
        }
        pushDebugEvent('polling', 'poll-cycle', {
          stream: 'session', sessionId, cursorBefore: cursor, cursorAfter: data.next_cursor, generation: myGeneration
        })
        if (isCurrentGeneration) {
          sessionConn.setCursor(data.next_cursor)
          sessionCursors[sessionId] = data.next_cursor
          sessionConn.retryCount.value = 0
          sessionConn.transition(connectedFamilyTarget(sessionConn), sessionId)
        } else {
          sessionCursors[sessionId] = data.next_cursor
        }

      } catch (err) {
        if (err.name === 'AbortError') {
          continue
        }
        console.warn(`[Session poll] Connection error for session ${sessionId} (retry ${sessionConn.retryCount.value + 1}):`, err.message || err)
        pushDebugEvent('polling', 'poll-error', {
          stream: 'session', sessionId, message: err.message || String(err), retryCount: sessionConn.retryCount.value + 1, generation: myGeneration
        })
        if (!sessionConn.isCurrentGeneration(myGeneration) || currentSessionId.value !== sessionId) {
          // Superseded by a session switch/disconnect while this fetch was in flight —
          // let the while-loop's own guard end the loop, no state to touch here.
          continue
        }
        sessionConn.retryCount.value++
        sessionConn.transition('backoff', sessionId)
        const delay = Math.min(2000 * sessionConn.retryCount.value, 30000)
        await sessionConn.scheduleBackoff(delay)
        if (sessionConn.isCurrentGeneration(myGeneration) && currentSessionId.value === sessionId) {
          sessionConn.transition('connecting', sessionId)
        }
      }
    }
  }

  async function connectSession(sessionId) {
    // Tear down any existing session connection first — always synchronous now, no 3-second
    // exit budget needed (see disconnect()'s own comment).
    await disconnectSession()

    currentSessionId.value = sessionId

    // Issue #1000: Prefer cursor from loadMessages() REST response (aligned with loaded
    // history). Falls back to the cross-session cache (resuming a previously-viewed
    // session exactly where it left off), then to a fresh API bootstrap for a genuinely
    // first-time connection.
    const messageStore = useMessageStore()
    const restCursor = messageStore.loadedEventCursors.get(sessionId)
    let initialCursor
    if (restCursor !== undefined) {
      initialCursor = restCursor
      messageStore.loadedEventCursors.delete(sessionId)
    } else if (sessionCursors[sessionId] !== undefined) {
      initialCursor = sessionCursors[sessionId]
    } else {
      try {
        const result = await api.get(`/api/poll/session/${sessionId}/cursor`)
        initialCursor = result?.cursor ?? 0
      } catch {
        initialCursor = 0
      }
    }
    sessionCursors[sessionId] = initialCursor

    // A session switch may have raced this cursor-resolution await — if a newer connect()
    // already landed for a different session, don't start a loop for a stale one.
    if (currentSessionId.value !== sessionId) return

    const myGeneration = sessionConn.connect(initialCursor, sessionId)
    _runSessionPollLoop(sessionId, myGeneration)
  }

  async function disconnectSession() {
    const sessionId = currentSessionId.value
    sessionConn.disconnect(sessionId)
    currentSessionId.value = null
  }

  // Issue #1974/#2129 stage 5b: clears the cross-session cursor cache entry for one session,
  // forcing its NEXT connect() to start fresh from cursor 0 instead of resuming an old
  // position — used right after disconnecting a session whose server-side history/epoch was
  // just reset/restarted, so the stale pre-reset cursor is never resumed from.
  function resetSessionCursor(sessionId) {
    delete sessionCursors[sessionId]
  }

  // Full teardown of a permanently-deleted session's cached cursor, called from
  // session.js's deleteSession()/removeSessionsFromStores(). Unlike resetSessionCursor()
  // above (always the single current session being reset/restarted by the user), this can
  // be called for many sessions at once, most of which are NOT the current session.
  function cleanupSessionPollingState(sessionId) {
    delete sessionCursors[sessionId]
  }

  // Epic #1990 stage 5b (AC3/UC3, decision 5): an archived/ephemeral-with-no-archives
  // session view never starts a poll loop at all — this makes that an explicit, inspectable
  // state instead of an implicit absence (wsStore.currentSessionId/sessionConn.state simply
  // never being touched). Called from SessionView.vue's archive-mode activation.
  function markSessionArchived(sessionId) {
    // Builder-review finding: without tearing down first, marking the CURRENTLY-live
    // session's own connection archived (same sessionId, e.g. opening /archive/X for the
    // session already being actively viewed) left its still-running poll loop's generation
    // and currentSessionId both unchanged — its very next successful poll would overwrite
    // 'archived' straight back to 'connected' underneath the archive view. Routing through
    // the same disconnect() every other state transition uses bumps the generation (so the
    // stale loop's next cycle sees itself superseded and stops self-correcting) and resets
    // backendStatus/hasEverConnected, consistent with every other path that changes which
    // session this connection targets.
    sessionConn.disconnect(currentSessionId.value)
    currentSessionId.value = sessionId
    sessionConn.markArchived(sessionId)
  }

  // ========== PAGE VISIBILITY ==========
  function setupVisibilityHandler() {
    if (visibilityUnsubscribe) visibilityUnsubscribe()

    const handler = () => {
      if (document.visibilityState === 'visible') {
        pushDebugEvent('polling', 'visibility-reconnect', { sessionId: currentSessionId.value })
        uiConn.abortInFlight()
        sessionConn.abortInFlight()
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
  // Each handler: (payload, sessionId) => void. sessionId is only meaningful for
  // SESSION_EVENT_HANDLERS (always equal to the current session, enforced by
  // dispatchEvent below); UI_EVENT_HANDLERS entries read whatever session id they need
  // from payload.data.
  const UI_EVENT_HANDLERS = {
    state_change: (payload) => {
      const sessionStore = useSessionStore()
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
          useMcpStore().fetchMcpStatus(changedSessionId)
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
        // Epic #1990 stage 5b: only clears the cross-session cursor CACHE entry — this is
        // independent of sessionConn's own live cursor (private to whichever session is
        // actively being polled), so this is safe to run even if a resync is concurrently
        // in flight for this exact session (the "reset arriving during a history load" race
        // the stage 5b plan calls out): the in-flight resync's own generation-guarded cursor
        // adoption (in _runSessionPollLoop above) still runs to completion independently and
        // writes its own authoritative value into this same cache afterward.
        delete sessionCursors[resetSessionId]
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
    // (the UI poll loop below) is responsible for the surrounding bookkeeping
    // (vanished-session cleanup, cursor adoption, stopping further per-event
    // processing of that batch) — this handler is only the data-level resync
    // itself, same as the pre-stage-5a data.reset branch did.
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
    // the session poll loop below never actually reaches this entry via
    // dispatchEvent() — it intercepts stream_reset itself and calls
    // messageStore.loadMessages() directly, gated on its own isCurrentGeneration
    // check rather than dispatchEvent()'s separate (sessionStore-sourced)
    // currentSessionId guard, which can diverge from it mid-resync. Kept as a
    // correct, self-consistent fallback in case anything else ever dispatches
    // this type through the normal path.
    stream_reset: async (payload, sessionId) => {
      await useMessageStore().loadMessages(sessionId)
    },
  }

  function dispatchEvent(payload, stream, sessionId) {
    if (stream === 'session') {
      // The one place the current-session guard lives now (AC1). UI-stream events are
      // never guarded — unchanged from today: handleUIMessage never checked currentSessionId,
      // because UI events carry their own target session id in their payload and can
      // legitimately apply to a session that isn't currently open (e.g. state_change for a
      // background session).
      const sessionStore = useSessionStore()
      if (sessionStore.currentSessionId !== sessionId) return
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
    // handler — currently only stream_reset's resync — can await it (issue
    // #1990 stage 5a, AC7). Every other handler is synchronous, so awaiting
    // this return value elsewhere is a no-op for them.
    return handler(payload, sessionId)
  }

  // ========== RETURN ==========
  return {
    uiState: uiConn.state,
    uiRetryCount: uiConn.retryCount,
    // Issue #1989: Backend-health signal carried on every successful poll response ("ok" /
    // "unreachable" / "degraded") — independent of either transport state, which stays
    // 'connected' throughout a pure Backend-side outage (Frontend keeps answering polls
    // normally). Session's own backendStatus tracks the same signal from the session-poll
    // channel's responses; the two are expected to agree in practice (both reflect the same
    // Backend), exposed separately per-stream per decision 1.
    uiBackendStatus: uiConn.backendStatus,
    sessionState: sessionConn.state,
    sessionRetryCount: sessionConn.retryCount,
    sessionBackendStatus: sessionConn.backendStatus,
    sessionHasEverConnected: sessionConn.hasEverConnected,
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
    startUIPolling,
    stopUIPolling,
    setupVisibilityHandler,
    connectSession,
    disconnectSession,
    resetSessionCursor,
    cleanupSessionPollingState,
    markSessionArchived,
    sendMessage,
    sendPermissionResponse,
    sendPermissionResponseWithInput,
    interruptSession,
    connectLegion,
    disconnectLegion,
  }
})
