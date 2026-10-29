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
// Pacing rules:
//   - adaptive page budget: starts at PAGE_BUDGET_BYTES, doubles while pages complete quickly
//     (< FAST_PAGE_MS) up to MAX_PAGE_BUDGET_BYTES, halves when a page is slow (> SLOW_PAGE_MS)
//     down to MIN_PAGE_BUDGET_BYTES — a fast link drains the history in a handful of large
//     requests, a slow one keeps small pages;
//   - inter-page gap proportional to the measured page time, GAP_FACTOR x lastPageMs clamped to
//     [0, MAX_GAP_MS]: near zero on a fast link, a gentle duty cycle on a slow one;
//   - paused while the connection is connecting / resyncing / backoff / unreachable, while the
//     last poll batch was a catch-up burst, while a live batch landed within QUIET_MS, while the
//     tab is hidden, and while a user REST action (send / permission / interrupt / ...) runs;
//   - page fetches use low fetch priority and are aborted on session switch / reset;
//   - requestUntil(sessionId, messageId) (issue #2030 stage 2: turn navigation) is a priority
//     request: `before=` pages of JUMP_PAGE_BUDGET_BYTES, no inter-page gap, until the message is
//     loaded (checked by id; cursors stay opaque). It keeps the pause rules (live catch-up,
//     connection state, hidden tab, user requests), works in manual mode too, and can be cancelled;
//   - slow-link mode (saveData, 2g, or measured first-window throughput < SLOW_LINK_BPS when the
//     Network Information API is absent): NO automatic backfill — older history loads on demand
//     (scrolling near the top, the banner's button) only.
// There is no ceiling: on a good link backfill runs until `has_more_before` is false.

export const PAGE_BUDGET_BYTES = 256 * 1024
// Pages fetched on behalf of a turn jump (overshoot past the target is at most one page).
export const JUMP_PAGE_BUDGET_BYTES = 512 * 1024
export const MIN_PAGE_BUDGET_BYTES = 64 * 1024
// The server caps a `before` page at 4 MiB / 5000 records (backend MAX_BYTES_CAP / MAX_RECORDS_CAP).
export const MAX_PAGE_BUDGET_BYTES = 4 * 1024 * 1024
export const MAX_PAGE_RECORDS = 5000
export const FAST_PAGE_MS = 500
export const SLOW_PAGE_MS = 2000
export const GAP_FACTOR = 0.5
export const MAX_GAP_MS = 2000
// Applying a page runs on the main thread: a page that takes longer than this to apply must not
// grow the budget, one that takes much longer shrinks it.
export const APPLY_OK_MS = 100
export const APPLY_SLOW_MS = 250
const ERROR_BACKOFF_BASE_MS = 500
export const QUIET_MS = 1500
// A single poll batch this large means the stream is catching up (reconnect / burst).
export const CATCHUP_EVENT_COUNT = 100
export const SLOW_LINK_BPS = 100 * 1024
// Longest the history index waits for the agents/tasks snapshot requests before it is requested anyway.
const INDEX_AFTER_SNAPSHOTS_MAX_MS = 5000
const WAIT_POLL_MS = 250
const PAGE_INACTIVITY_MS = 30000
const MAX_CONSECUTIVE_ERRORS = 5
const BLOCKING_CONNECTION_STATES = new Set(['idle', 'connecting', 'resyncing', 'backoff', 'unreachable'])

/** Next page budget from how long the last page took: grow when fast, shrink when slow. */
export function nextPageBudget(budget, pageMs, applyMs = 0) {
  if (pageMs > SLOW_PAGE_MS || applyMs > APPLY_SLOW_MS) return Math.max(MIN_PAGE_BUDGET_BYTES, Math.floor(budget / 2))
  if (pageMs < FAST_PAGE_MS && applyMs <= APPLY_OK_MS) return Math.min(MAX_PAGE_BUDGET_BYTES, budget * 2)
  return budget
}

/** Delay before the next page: proportional to the last page's duration, bounded. */
export function backfillGapMs(lastPageMs) {
  return Math.min(MAX_GAP_MS, Math.max(0, GAP_FACTOR * (lastPageMs || 0)))
}

// DevTools-visible timing of each page's fetch and apply (Performance panel / getEntriesByName).
function markPerf(name, startTime, duration) {
  try {
    performance.measure(name, { start: startTime - performance.timeOrigin, duration })
  } catch {
    // Performance timeline unavailable (tests, old browsers)
  }
}

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

  /** A user-driven request is pending (an on-demand page or a turn jump): no gap, no live-activity pause. */
  function isPriority(loop) {
    return loop.demand > 0 || !!loop.until
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
        if (mode === 'manual' && !isPriority(loop)) {
          setStatus(sessionId, { paused: null })
          await sleep(WAIT_POLL_MS)
          if (blockedReason(sessionId, history, true) === 'not-current') break
          continue
        }

        const isDemand = () => isPriority(loop)
        if (!(await waitUnblocked(sessionId, loop, isDemand))) break

        // Inter-page gap (bypassed by an explicit on-demand request).
        if (!isDemand() && loop.lastPageEndedAt != null) {
          const gap = backfillGapMs(loop.lastPageMs)
          const remaining = gap - (now() - loop.lastPageEndedAt)
          if (remaining > 0) {
            await sleep(remaining)
            continue // re-evaluate every condition after the wait
          }
        }

        const wasDemand = isPriority(loop)
        const controller = store.claimBackfill(sessionId)
        setStatus(sessionId, { loading: true, error: null })
        const startedAt = now()
        try {
          const page = await store.fetchOlderPage(sessionId, {
            // An explicit user request (scroll near the top, the banner button) never uses a grown
            // budget: the user is waiting for exactly that page.
            signal: controller.signal, maxBytes: loop.until ? JUMP_PAGE_BUDGET_BYTES : wasDemand ? PAGE_BUDGET_BYTES : loop.budget, maxRecords: MAX_PAGE_RECORDS, inactivityMs: PAGE_INACTIVITY_MS,
          })
          if (controller.signal.aborted || loops.get(sessionId) !== loop) break
          loop.lastPageMs = now() - startedAt
          if (!page) break
          markPerf('history-backfill-fetch', startedAt, loop.lastPageMs)

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

          const applyStartedAt = now()
          store.applyFetchedOlderPage(sessionId, page, { source: history.source || 'load' })
          loop.lastPageEndedAt = now()
          const applyMs = loop.lastPageEndedAt - applyStartedAt
          markPerf('history-backfill-apply', applyStartedAt, applyMs)
          loop.budget = nextPageBudget(loop.budget, loop.lastPageMs, applyMs)
          loop.lastPageMs += applyMs // the gap covers the main-thread cost of the page too
          consecutiveErrors = 0
          if (loop.demand > 0) loop.demand = Math.max(0, loop.demand - 1)
          if (loop.until && messageStore().isMessageLoaded(sessionId, loop.until.messageId)) finishUntil(sessionId, loop)
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
              loop.budget = PAGE_BUDGET_BYTES
              scheduleIndex(sessionId) // the replaced suffix dropped the old index
              continue
            } catch (restartErr) {
              if (controller.signal.aborted || loops.get(sessionId) !== loop) break
              err = restartErr // counted as an ordinary failure below
            }
          }
          loop.budget = Math.max(MIN_PAGE_BUDGET_BYTES, Math.floor(loop.budget / 2)) // a failed/timed-out page shrinks it
          consecutiveErrors++
          pushDebugEvent('message', 'history-backfill-error', { sessionId, message: err?.message, consecutiveErrors })
          if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
            setStatus(sessionId, { loading: false, error: err?.message || 'Failed to load older history' })
            break
          }
          await sleep(Math.min(30000, ERROR_BACKOFF_BASE_MS * 2 ** consecutiveErrors))
        } finally {
          store.releaseBackfill(sessionId, controller)
          setStatus(sessionId, { loading: false })
        }
      }
    } finally {
      if (loops.get(sessionId) === loop) loops.delete(sessionId)
      // The loop ended (history complete, session switched, stopped, gave up) before the target
      // was loaded: the jump can't be satisfied here.
      if (loop.until) {
        const reached = messageStore().isMessageLoaded(sessionId, loop.until.messageId)
        finishUntil(sessionId, loop, reached ? null : (messageStore().isHistoryComplete(sessionId) ? 'not-found' : 'aborted'))
      }
      const history = messageStore().getHistoryState(sessionId)
      if (!history || !history.hasMoreBefore) clearStatus(sessionId)
      else setStatus(sessionId, { loading: false, paused: null })
    }
  }

  /**
   * Fetches the server history index for a tail-loaded session once `after` (the agents/tasks
   * snapshot requests) has settled — low priority, never in the window/page path, and not at all
   * on a slow link (there it is fetched only when the user presses Prev) or once the whole
   * history is loaded. Idempotent; a replaced suffix (reset / history_id change / gap-replace)
   * dropped the old index, so the next call refetches.
   */
  function scheduleIndex(sessionId, after = []) {
    // A hung snapshot request must not hold the index (and Prev) back for good: cap the wait.
    let timer
    const cap = new Promise(resolve => { timer = setTimeout(resolve, INDEX_AFTER_SNAPSHOTS_MAX_MS) })
    return Promise.race([Promise.allSettled(after), cap]).then(() => {
      clearTimeout(timer)
      const store = messageStore()
      if (store.isHistoryComplete(sessionId)) return null
      if (getMode(sessionId) === 'manual') return null
      return store.ensureHistoryIndex(sessionId)
    })
  }

  /** Settles the loop's pending turn jump (`reason` null == the target is loaded). */
  function finishUntil(sessionId, loop, reason = null) {
    const until = loop.until
    if (!until) return
    loop.until = null
    if (statusBySession.value.get(sessionId)?.jump?.messageId === until.messageId) setStatus(sessionId, { jump: null })
    if (reason) {
      const err = new Error(reason === 'cancelled' ? 'Jump cancelled' : `Turn jump failed: ${reason}`)
      err.reason = reason
      until.reject(err)
    } else {
      until.resolve()
    }
  }

  /** Begins (or continues) backfilling `sessionId`. At most one loop per session. */
  function start(sessionId) {
    const history = messageStore().getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) return
    if (loops.has(sessionId)) return
    const loop = { demand: 0, lastPageMs: 0, lastPageEndedAt: null, forcedAuto: false, budget: PAGE_BUDGET_BYTES }
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

  /**
   * Turn-navigation jump: loads older history with priority `before=` pages until the message
   * `messageId` is part of the loaded suffix. Returns `{ promise, cancel }`; `promise` rejects
   * with `err.reason` 'cancelled' | 'aborted' | 'not-found' | 'superseded'. `meta`
   * (`{ ordinal, fromOffset, toOffset }`) is published on the status as `jump` for progress UI.
   * The loaded suffix stays valid whatever happens (pages are applied as usual).
   */
  function requestUntil(sessionId, messageId, meta = {}) {
    const store = messageStore()
    if (store.isMessageLoaded(sessionId, messageId)) return { promise: Promise.resolve(), cancel() {} }
    const history = store.getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) {
      const err = new Error('Turn jump failed: not-found')
      err.reason = 'not-found'
      return { promise: Promise.reject(err), cancel() {} }
    }
    if (!loops.has(sessionId)) start(sessionId)
    const loop = loops.get(sessionId)
    finishUntil(sessionId, loop, 'superseded')
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    promise.catch(() => {}) // callers handle it; never an unhandled rejection
    const until = { messageId, resolve, reject }
    loop.until = until
    setStatus(sessionId, { jump: { messageId, ...meta }, error: null })
    return { promise, cancel: () => { if (loop.until === until) finishUntil(sessionId, loop, 'cancelled') } }
  }

  /** Cancels the session's pending turn jump (the loaded suffix stays valid). */
  function cancelJump(sessionId) {
    const loop = loops.get(sessionId)
    if (loop) finishUntil(sessionId, loop, 'cancelled')
  }

  /** 'auto' | 'manual' (slow link, nothing loads unrequested) for this session right now. */
  function getMode(sessionId) {
    const history = messageStore().getHistoryState(sessionId)
    return currentModeFor(sessionId, history, loops.get(sessionId))
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
    scheduleIndex,
    requestUntil,
    cancelJump,
    getMode,
    loadAll,
    retry,
    setScrollInteraction,
    isRunning: (sessionId) => loops.has(sessionId),
  }
}

export const historyBackfill = createHistoryBackfill()

/**
 * Starts backfill for `sessionId` when only a suffix of its history is loaded, and schedules its
 * history index (after `after`, the snapshot requests that must settle first — issue #2030 stage 2).
 */
export function startBackfillIfIncomplete(sessionId, after = []) {
  if (useMessageStore().isHistoryComplete(sessionId)) return
  historyBackfill.start(sessionId)
  historyBackfill.scheduleIndex(sessionId, after)
}

export function useHistoryBackfill() {
  return historyBackfill
}
