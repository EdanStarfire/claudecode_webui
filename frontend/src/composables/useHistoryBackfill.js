import { ref } from 'vue'
import { getMutationsInFlight } from '@/utils/api'
import { useMessageStore } from '@/stores/message'
import { usePollingStore } from '@/stores/polling'
import { pushDebugEvent } from '@/composables/useDebugBuffer'

// Issue #2030 (stage 1b): background backfill of older history behind a tail-first load.
//
// The session opens on its newest turn-snapped window; this scheduler then pulls the older
// history in pages (`before=<cursor>`) and prepend-merges each one. It is deliberately gentle —
// on a slow link the first paint of the newest messages matters far more than loading all of
// the history — and it must never compete with live events or user actions.
//
// Minimum pacing rules (fixed here; adaptive refinements are a later stage):
//   - fixed page budget (PAGE_BUDGET_BYTES), inter-page gap max(MIN_GAP_MS, 2 x last page time);
//   - paused while the connection is connecting / resyncing / backoff / unreachable, while the
//     last poll batch was a catch-up burst, while a live batch landed within QUIET_MS, while the
//     tab is hidden, and while a user REST action (send / permission / interrupt / ...) runs;
//   - page fetches use low fetch priority and are aborted on session switch / reset;
//   - slow-link mode (saveData, 2g, or measured first-window throughput < SLOW_LINK_BPS when the
//     Network Information API is absent): NO automatic backfill — older history loads on demand
//     (scrolling near the top, the banner's button) only.
// There is no ceiling: on a good link backfill runs until `has_more_before` is false.

export const PAGE_BUDGET_BYTES = 256 * 1024
export const MIN_GAP_MS = 500
export const QUIET_MS = 1500
// A single poll batch this large means the stream is catching up (reconnect / burst).
export const CATCHUP_EVENT_COUNT = 100
export const SLOW_LINK_BPS = 100 * 1024
const WAIT_POLL_MS = 250
const PAGE_INACTIVITY_MS = 30000
const MAX_CONSECUTIVE_ERRORS = 5
const BLOCKING_CONNECTION_STATES = new Set(['idle', 'connecting', 'resyncing', 'backoff', 'unreachable'])

function defaultSleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function defaultIdleSlot() {
  return new Promise(resolve => {
    if (typeof requestIdleCallback === 'function') requestIdleCallback(() => resolve(), { timeout: 1000 })
    else setTimeout(resolve, 0)
  })
}

function defaultMutationsInFlight() {
  try {
    return getMutationsInFlight()
  } catch {
    return 0 // module mocked without the counter (tests)
  }
}

/** Slow-link detection per the plan: Network Information API when present, else throughput. */
export function detectSlowLink(history, nav = typeof navigator !== 'undefined' ? navigator : null) {
  const connection = nav?.connection
  if (connection) {
    return !!connection.saveData || connection.effectiveType === 'slow-2g' || connection.effectiveType === '2g'
  }
  return typeof history?.windowBps === 'number' && history.windowBps < SLOW_LINK_BPS
}

/**
 * `deps` is injectable for tests; production uses the stores. Returns the scheduler API plus a
 * reactive `statusBySession` map: `{ mode: 'auto' | 'manual', paused: string | null,
 * loading: boolean, error: string | null }`.
 */
export function createHistoryBackfill(deps = {}) {
  const messageStore = () => (deps.messageStore ? deps.messageStore() : useMessageStore())
  const pollingStore = () => (deps.pollingStore ? deps.pollingStore() : usePollingStore())
  const now = deps.now || (() => Date.now())
  const sleep = deps.sleep || defaultSleep
  const idleSlot = deps.idleSlot || defaultIdleSlot
  const mutationsInFlight = deps.mutationsInFlight || defaultMutationsInFlight
  const isHidden = deps.isHidden || (() => typeof document !== 'undefined' && document.visibilityState === 'hidden')
  const isSlowLink = deps.isSlowLink || ((history) => detectSlowLink(history))

  const statusBySession = ref(new Map())
  // sessionId -> { token, demand: number, lastPageMs, lastPageEndedAt, forcedAuto }
  const loops = new Map()
  // sessionId -> { gestureActive, nearTop } reported by the message list
  const scrollInteraction = new Map()

  function setStatus(sessionId, patch) {
    const previous = statusBySession.value.get(sessionId)
    const merged = { mode: 'auto', paused: null, loading: false, error: null, ...(previous || {}), ...patch }
    // The wait loops call this every tick; only publish a real change.
    if (previous && Object.keys(merged).every(key => merged[key] === previous[key])) return
    const next = new Map(statusBySession.value)
    next.set(sessionId, merged)
    statusBySession.value = next
  }

  function clearStatus(sessionId) {
    if (!statusBySession.value.has(sessionId)) return
    const next = new Map(statusBySession.value)
    next.delete(sessionId)
    statusBySession.value = next
  }

  function currentModeFor(sessionId, history, loop) {
    if (loop?.forcedAuto) return 'auto'
    return isSlowLink(history) ? 'manual' : 'auto'
  }

  /** Why the loop must not fetch right now (null == free to go). `demand` = user-driven page. */
  function blockedReason(sessionId, history, demand) {
    const polling = pollingStore()
    if (polling.currentSessionId !== sessionId) return 'not-current'
    if (isHidden()) return 'hidden'
    if (mutationsInFlight() > 0) return 'user-request'
    // Archive views have no live stream to protect.
    if (history.source !== 'archive') {
      if (BLOCKING_CONNECTION_STATES.has(polling.sessionState)) return `connection-${polling.sessionState}`
      const batch = polling.getLastBatchInfo?.(sessionId)
      if (batch) {
        const age = now() - batch.at
        if (batch.count >= CATCHUP_EVENT_COUNT && age < QUIET_MS) return 'catching-up'
        if (!demand && age < QUIET_MS) return 'live-activity'
      }
    }
    return null
  }

  async function waitUnblocked(sessionId, loop, demand) {
    for (;;) {
      if (loops.get(sessionId) !== loop) return false
      const history = messageStore().getHistoryState(sessionId)
      if (!history || !history.hasMoreBefore) return false
      const reason = blockedReason(sessionId, history, demand())
      if (reason === 'not-current') return false
      setStatus(sessionId, { paused: reason })
      if (!reason) return true
      await sleep(WAIT_POLL_MS)
    }
  }

  async function runLoop(sessionId, loop) {
    let consecutiveErrors = 0
    try {
      while (loops.get(sessionId) === loop) {
        const store = messageStore()
        const history = store.getHistoryState(sessionId)
        if (!history || !history.hasMoreBefore) break

        const mode = currentModeFor(sessionId, history, loop)
        setStatus(sessionId, { mode })
        // Manual mode only advances on demand (scroll near the top / banner button).
        if (mode === 'manual' && loop.demand === 0) {
          setStatus(sessionId, { paused: null })
          await sleep(WAIT_POLL_MS)
          if (blockedReason(sessionId, history, true) === 'not-current') break
          continue
        }

        const isDemand = () => loop.demand > 0
        if (!(await waitUnblocked(sessionId, loop, isDemand))) break

        // Inter-page gap (bypassed by an explicit on-demand request).
        if (!isDemand() && loop.lastPageEndedAt != null) {
          const gap = Math.max(MIN_GAP_MS, 2 * (loop.lastPageMs || 0))
          const remaining = gap - (now() - loop.lastPageEndedAt)
          if (remaining > 0) {
            await sleep(remaining)
            continue // re-evaluate every condition after the wait
          }
        }

        const wasDemand = loop.demand > 0
        const controller = store.claimBackfill(sessionId)
        setStatus(sessionId, { loading: true, error: null })
        const startedAt = now()
        try {
          const page = await store.fetchOlderPage(sessionId, {
            signal: controller.signal, maxBytes: PAGE_BUDGET_BYTES, inactivityMs: PAGE_INACTIVITY_MS,
          })
          if (controller.signal.aborted || loops.get(sessionId) !== loop) break
          loop.lastPageMs = now() - startedAt
          if (!page) break

          // Applying prepends content above the viewport: wait out an active scroll gesture
          // unless the viewport is already within a screen of the top.
          for (;;) {
            const interaction = scrollInteraction.get(sessionId)
            if (!interaction?.gestureActive || interaction.nearTop) break
            if (controller.signal.aborted || loops.get(sessionId) !== loop) break
            await sleep(WAIT_POLL_MS)
          }
          if (controller.signal.aborted || loops.get(sessionId) !== loop) break
          await idleSlot()
          if (controller.signal.aborted || loops.get(sessionId) !== loop) break

          store.applyFetchedOlderPage(sessionId, page, { source: history.source || 'load' })
          loop.lastPageEndedAt = now()
          consecutiveErrors = 0
          if (wasDemand) loop.demand = Math.max(0, loop.demand - 1)
        } catch (caught) {
          let err = caught
          if (controller.signal.aborted || loops.get(sessionId) !== loop) break
          if (err?.isHistoryReset) {
            // Cursor invalid / file rewritten: the loaded suffix can't be extended — restart
            // from a fresh tail window (generation bump) and continue backfilling from there.
            pushDebugEvent('message', 'history-restart', { sessionId })
            try {
              // An archive view owns its signal (it must not claim the live session's shared
              // hydration tracker for the same id); a live session claims it like any resync.
              await store.loadMessages(sessionId, null, 0, {
                window: 'tail', baseUrl: history.baseUrl, source: history.source || 'load',
                ...(history.source === 'archive' ? { signal: controller.signal } : {}),
              })
              loop.lastPageEndedAt = null
              continue
            } catch (restartErr) {
              if (controller.signal.aborted || loops.get(sessionId) !== loop) break
              err = restartErr // counted as an ordinary failure below
            }
          }
          consecutiveErrors++
          pushDebugEvent('message', 'history-backfill-error', { sessionId, message: err?.message, consecutiveErrors })
          if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
            setStatus(sessionId, { loading: false, error: err?.message || 'Failed to load older history' })
            break
          }
          await sleep(Math.min(30000, MIN_GAP_MS * 2 ** consecutiveErrors))
        } finally {
          store.releaseBackfill(sessionId, controller)
          setStatus(sessionId, { loading: false })
        }
      }
    } finally {
      if (loops.get(sessionId) === loop) loops.delete(sessionId)
      const history = messageStore().getHistoryState(sessionId)
      if (!history || !history.hasMoreBefore) clearStatus(sessionId)
      else setStatus(sessionId, { loading: false, paused: null })
    }
  }

  /** Begins (or continues) backfilling `sessionId`. At most one loop per session. */
  function start(sessionId) {
    const history = messageStore().getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) return
    if (loops.has(sessionId)) return
    const loop = { demand: 0, lastPageMs: 0, lastPageEndedAt: null, forcedAuto: false }
    loops.set(sessionId, loop)
    setStatus(sessionId, { mode: currentModeFor(sessionId, history, loop), paused: null, error: null })
    runLoop(sessionId, loop)
  }

  function stop(sessionId) {
    loops.delete(sessionId)
    messageStore().abortBackfill(sessionId)
  }

  function stopAll() {
    for (const sessionId of [...loops.keys()]) stop(sessionId)
  }

  /** Requests one older page now (scroll near the top, the banner's button). */
  function requestOlder(sessionId) {
    const history = messageStore().getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) return
    if (!loops.has(sessionId)) start(sessionId)
    const loop = loops.get(sessionId)
    if (loop && loop.demand === 0) loop.demand = 1
  }

  /** Leaves slow-link/manual mode for this session: backfill the rest automatically (paced). */
  function loadAll(sessionId) {
    const history = messageStore().getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) return
    if (!loops.has(sessionId)) start(sessionId)
    const loop = loops.get(sessionId)
    if (loop) {
      loop.forcedAuto = true
      setStatus(sessionId, { mode: 'auto', error: null })
    }
  }

  /** Retry after the loop gave up on repeated errors. */
  function retry(sessionId) {
    stop(sessionId)
    start(sessionId)
  }

  /** The message list reports scroll state so a prepend never lands mid-gesture. */
  function setScrollInteraction(sessionId, interaction) {
    scrollInteraction.set(sessionId, interaction)
  }

  return {
    statusBySession,
    start,
    stop,
    stopAll,
    requestOlder,
    loadAll,
    retry,
    setScrollInteraction,
    isRunning: (sessionId) => loops.has(sessionId),
  }
}

export const historyBackfill = createHistoryBackfill()

/** Starts backfill for `sessionId` when only a suffix of its history is loaded. */
export function startBackfillIfIncomplete(sessionId) {
  if (!useMessageStore().isHistoryComplete(sessionId)) historyBackfill.start(sessionId)
}

export function useHistoryBackfill() {
  return historyBackfill
}
