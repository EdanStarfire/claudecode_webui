import { ref } from 'vue'
import { getMutationsInFlight } from '@/utils/api'
import { useMessageStore } from '@/stores/message'
import { usePollingStore } from '@/stores/polling'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { pushDebugEvent } from '@/composables/useDebugBuffer'
import { windowBytes } from '@/utils/historyIntervals'
import {
  TRIM_MIN_INTERVAL_MS, TRIM_RETRY_MS, backgroundKeepBytes, getHistoryBudgetBytes, trimTargetBytes,
} from '@/utils/historyBudget'

// Issue #2030 (stage 1b), reworked by #2331: on-demand loading of older history behind a tail-first load.
//
// The session opens on its newest turn-snapped window. Nothing else loads unless the user asks: a
// scroll toward the top (`requestOlder`, via the message list's scroll prefetch) or a jump. The loop
// below is the executor of those requests: it fetches `before=<cursor>` pages while a request is
// pending and prepend-merges each one, then ends (there is no background extender any more).
//
// Rules:
//   - adaptive page budget: starts at PAGE_BUDGET_BYTES, doubles while pages complete quickly
//     (< FAST_PAGE_MS) up to MAX_PAGE_BUDGET_BYTES, halves when a page is slow (> SLOW_PAGE_MS)
//     down to MIN_PAGE_BUDGET_BYTES; a prefetch page uses the adaptive budget, an urgent one
//     (the user is already waiting at the top) the base budget;
//   - a page is held back while the connection is connecting / resyncing / backoff / unreachable,
//     while the last poll batch was a catch-up burst, while the tab is hidden, and while a user REST
//     action (send / permission / interrupt / ...) runs;
//   - page fetches use low fetch priority and are aborted on session switch / reset;
//   - jumps (issue #2030 stage 3 PR 3C-1: turn navigation, search hits) do not extend the suffix:
//     jumpAround() fetches ONE bounded `around=` window as the pinned island (message store
//     loadAround), requestIslandOlder() / requestIslandNewer() grow that island by one page on
//     user demand (scrolling near its top, the gap row's "Load newer"). They can be cancelled;
//   - slow-link mode (saveData, 2g, or measured first-window throughput < SLOW_LINK_BPS when the
//     Network Information API is absent) only changes jump confirms, the search page size and the
//     history index (fetched when the user presses Prev); scroll prefetch loads as usual.
// Memory budget (issue #2030 stage 4 PR 4d, utils/historyBudget.js): pages the user asks for are never
// refused by the budget (64 MiB of history file on a desktop, 32 MiB on a phone). When the loaded
// history exceeds it (explicit user scrolling, live growth) an IDLE trim releases the OLD end back to
// 0.75 x budget — only while no gesture is active, nothing is in flight, and the message list
// reports the removed rows are >= 12 viewports above the viewport (the scroll prefetch fires
// within 8, so it can never ask for a page the trim just removed); at most once per 30 s. Scrolling
// back up refetches the released page. A session that stops being the current one keeps only its
// newest min(8 MiB, budget/4).

export const PAGE_BUDGET_BYTES = 256 * 1024
export const MIN_PAGE_BUDGET_BYTES = 64 * 1024
// The server caps a `before` page at 4 MiB / 5000 records (backend MAX_BYTES_CAP / MAX_RECORDS_CAP).
export const MAX_PAGE_BUDGET_BYTES = 4 * 1024 * 1024
export const MAX_PAGE_RECORDS = 5000
export const FAST_PAGE_MS = 500
export const SLOW_PAGE_MS = 2000
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
const PERMANENT_TRIM_REASONS = new Set(['no-history', 'no-ledger', 'ledger-chain', 'opaque-cursor'])
const BLOCKING_CONNECTION_STATES = new Set(['idle', 'connecting', 'resyncing', 'backoff', 'unreachable'])

/** Next page budget from how long the last page took: grow when fast, shrink when slow. */
export function nextPageBudget(budget, pageMs, applyMs = 0) {
  if (pageMs > SLOW_PAGE_MS || applyMs > APPLY_SLOW_MS) return Math.max(MIN_PAGE_BUDGET_BYTES, Math.floor(budget / 2))
  if (pageMs < FAST_PAGE_MS && applyMs <= APPLY_OK_MS) return Math.min(MAX_PAGE_BUDGET_BYTES, budget * 2)
  return budget
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
  const budgetBytes = deps.budgetBytes || (() => getHistoryBudgetBytes())
  const BACKGROUND_TRIM_DELAY_MS = deps.backgroundTrimDelayMs ?? 30000
  const viewedSessionId = deps.viewedSessionId || (() => useSessionStore().currentSessionId)
  // No entry == the list never rendered (nothing to preserve); the ui store records whether a list follows the live end.
  const isStickyBottom = deps.isStickyBottom || ((sessionId) => useUIStore().stickyToBottomBySession.get(sessionId) !== false)

  const statusBySession = ref(new Map())
  // sessionId -> { demand: number, urgent, lastPageMs, budget }
  const loops = new Map()
  // sessionId -> { budget, lastPageMs }: the page-size / latency estimate outlives a loop (each demand burst starts a new one)
  const pageStats = new Map()
  // sessionId -> { gestureActive, nearTop } reported by the message list
  const scrollInteraction = new Map()
  // sessionId -> () => (() => void) | void: the message list's hook around an OLDER page being applied
  // (called right before; its return value right after) so it can keep the viewport on the same message.
  const pageApplyHooks = new Map()
  // sessionId -> () => { maxCutMessages } | null: the message list's answer to "how many leading
  // messages may an idle trim remove" (rows >= 12 viewports above the viewport) — absent == no list shown.
  const trimGuards = new Map()
  const trimTimers = new Map()
  const lastTrimAt = new Map()
  const leaveTimers = new Map()
  
  // `meta.trim`: the apply REMOVES rows above the viewport (a trim, or an island eviction of the near end),
  // so the list anchors on the message wherever the viewport is, not only near the top.
  function applyWithHook(sessionId, apply, meta = null) {
    let after = null
    try { after = pageApplyHooks.get(sessionId)?.(meta) } catch { after = null }
    try {
      return apply()
    } finally {
      try { after?.() } catch { /* a view hook must never fail a page */ }
    }
  }

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

  function currentModeFor(sessionId, history) {
    return isSlowLink(history) ? 'manual' : 'auto'
  }

  /** A user-driven request is pending (an on-demand page): no gap, no live-activity pause. */
  function isPriority(loop) {
    return loop.demand > 0
  }

  /** Why the loop must not fetch right now (null == free to go). */
  function blockedReason(sessionId, history) {
    const polling = pollingStore()
    if (polling.currentSessionId !== sessionId) return 'not-current'
    if (isHidden()) return 'hidden'
    if (mutationsInFlight() > 0) return 'user-request'
    // Archive views have no live stream to protect.
    if (history.source !== 'archive') {
      if (BLOCKING_CONNECTION_STATES.has(polling.sessionState)) return `connection-${polling.sessionState}`
      const batch = polling.getLastBatchInfo?.(sessionId)
      if (batch && batch.count >= CATCHUP_EVENT_COUNT && now() - batch.at < QUIET_MS) return 'catching-up'
    }
    return null
  }

  async function waitUnblocked(sessionId, loop) {
    for (;;) {
      if (loops.get(sessionId) !== loop) return false
      const history = messageStore().getHistoryState(sessionId)
      if (!history || !history.hasMoreBefore) return false
      const reason = blockedReason(sessionId, history)
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
        // Demand-only: the loop ends once every requested page has landed.
        if (!isPriority(loop)) break

        setStatus(sessionId, { mode: currentModeFor(sessionId, history) })
        if (!(await waitUnblocked(sessionId, loop))) break

        const controller = store.claimBackfill(sessionId)
        setStatus(sessionId, { loading: true, error: null })
        const startedAt = now()
        try {
          const page = await store.fetchOlderPage(sessionId, {
            // A page the user is already waiting for (urgent) never uses a grown budget; a prefetch may grow it
            // but never shrinks below the base (a smaller page only costs the user another round trip).
            signal: controller.signal, maxBytes: loop.urgent ? PAGE_BUDGET_BYTES : Math.max(PAGE_BUDGET_BYTES, loop.budget), maxRecords: MAX_PAGE_RECORDS, inactivityMs: PAGE_INACTIVITY_MS,
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
          applyWithHook(sessionId, () => store.applyFetchedOlderPage(sessionId, page, { source: history.source || 'load' }))
          requestTrimCheck(sessionId)
          const applyMs = now() - applyStartedAt
          markPerf('history-backfill-apply', applyStartedAt, applyMs)
          loop.budget = nextPageBudget(loop.budget, loop.lastPageMs, applyMs)
          loop.lastPageMs += applyMs // the prefetch distance covers the main-thread cost of the page too
          consecutiveErrors = 0
          if (loop.demand > 0) loop.demand = Math.max(0, loop.demand - 1)
          if (loop.demand === 0) loop.urgent = false
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
      pageStats.set(sessionId, { budget: loop.budget, lastPageMs: loop.lastPageMs })
      if (loops.get(sessionId) === loop) loops.delete(sessionId)
      const history = messageStore().getHistoryState(sessionId)
      if (!history || !history.hasMoreBefore) clearStatus(sessionId)
      else setStatus(sessionId, { loading: false, paused: null })
    }
  }

  // ---- memory budget: idle trim of the old end, background keep-bytes (issue #2030 stage 4 PR 4d) ----

  function loadedBytesOf(sessionId) {
    return messageStore().historyProgress?.(sessionId)?.loadedBytes ?? null
  }

  function cancelTrim(sessionId) {
    clearTimeout(trimTimers.get(sessionId))
    trimTimers.delete(sessionId)
  }

  /**
   * Arms an idle trim check for the current session when it holds more than the budget (cheap and
   * idempotent: the message list calls it on scroll, the loop after every page). The check retries
   * every TRIM_RETRY_MS while the conditions (idle, no gesture, far from the old edge) do not hold.
   */
  function requestTrimCheck(sessionId, delay = 2000) {
    if (trimTimers.has(sessionId)) return
    const loaded = loadedBytesOf(sessionId)
    if (loaded == null || loaded <= budgetBytes()) return
    trimTimers.set(sessionId, setTimeout(() => { trimTimers.delete(sessionId); runTrimCheck(sessionId) }, delay))
  }

  function trimBusy(sessionId) {
    // "Load full history" is an explicit request for everything: neither while it runs nor after it ended.
    return islandOps.has(sessionId) || !!statusBySession.value.get(sessionId)?.loading || (loops.get(sessionId)?.demand || 0) > 0
  }

  async function runTrimCheck(sessionId) {
    const store = messageStore()
    const history = store.getHistoryState(sessionId)
    if (!history || pollingStore().currentSessionId !== sessionId) return
    const budget = budgetBytes()
    const loaded = loadedBytesOf(sessionId)
    if (loaded == null || loaded <= budget) return
    const retry = (delay = TRIM_RETRY_MS) => requestTrimCheck(sessionId, delay)
    const sinceLast = now() - (lastTrimAt.get(sessionId) ?? -Infinity)
    if (sinceLast < TRIM_MIN_INTERVAL_MS) return retry(TRIM_MIN_INTERVAL_MS - sinceLast)
    const ready = () => {
      if (pollingStore().currentSessionId !== sessionId || isHidden()) return null
      if (scrollInteraction.get(sessionId)?.gestureActive || trimBusy(sessionId)) return null
      const guard = trimGuards.get(sessionId)?.()
      return guard && guard.maxCutMessages > 0 ? guard : null
    }
    if (!ready()) return retry()
    await idleSlot()
    const guard = ready() // the world may have moved while waiting for the idle slot
    if (!guard) return retry()
    // Rows far above the viewport go; the virtualizer keeps the viewport on its rows by key (browser-verified, 0 px drift).
    const result = store.trimOldEnd(sessionId, {
      targetBytes: trimTargetBytes(budget), maxCutMessages: guard.maxCutMessages, kind: 'budget',
    })
    if (result?.trimmed) {
      lastTrimAt.set(sessionId, now())
      markPerf('history-trim', now(), 0)
    }
    // Nothing to release by construction (no ledger / opaque cursors): the next page or scroll re-arms the check.
    if (!result?.trimmed && PERMANENT_TRIM_REASONS.has(result?.reason)) return
    const left = loadedBytesOf(sessionId)
    // Still over (the guard limited the cut, or nothing could be released yet): look again later.
    if (left != null && left > budget) retry(result?.trimmed ? TRIM_MIN_INTERVAL_MS : TRIM_RETRY_MS)
  }

  /**
   * The current session changed (polling.connectSession): the one left behind keeps only its newest
   * min(8 MiB, budget/4) once it has been idle for a moment, and the one entered cancels its own
   * pending background trim. A list scrolled up (not following the live end) is left alone — its
   * viewport would otherwise jump on return.
   */
  function onSessionSwitch(previousId, nextId) {
    if (nextId) { clearTimeout(leaveTimers.get(nextId)); leaveTimers.delete(nextId) }
    if (!previousId || previousId === nextId) return
    cancelTrim(previousId)
    clearTimeout(leaveTimers.get(previousId))
    leaveTimers.set(previousId, setTimeout(async () => {
      leaveTimers.delete(previousId)
      const store = messageStore()
      const keep = backgroundKeepBytes(budgetBytes())
      const loaded = loadedBytesOf(previousId)
      const shown = () => pollingStore().currentSessionId === previousId || viewedSessionId() === previousId
      if (loaded == null || loaded <= keep || shown()) return
      // Only a session whose list follows the live end: a saved scroll position is index based and a trim would shift it.
      if (!isStickyBottom(previousId)) return
      const guard = trimGuards.get(previousId)?.() ?? null
      await idleSlot()
      if (shown()) return
      store.trimOldEnd(previousId, { targetBytes: keep, maxCutMessages: guard ? guard.maxCutMessages : Infinity, kind: 'background' })
    }, BACKGROUND_TRIM_DELAY_MS))
  }

  function setTrimGuard(sessionId, fn) {
    trimGuards.set(sessionId, fn)
  }

  function clearTrimGuard(sessionId, fn) {
    if (trimGuards.get(sessionId) === fn) trimGuards.delete(sessionId)
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

  /** Begins the page loop for `sessionId` (ends at once without a pending demand). At most one loop per session. */
  // `initial` seeds the loop's flags BEFORE it first evaluates its block reasons (runLoop starts synchronously).
  function start(sessionId, initial = {}) {
    const history = messageStore().getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) return
    if (loops.has(sessionId)) return
    const loop = { demand: 0, urgent: false, lastPageMs: 0, budget: PAGE_BUDGET_BYTES, ...pageStats.get(sessionId), ...initial }
    loops.set(sessionId, loop)
    setStatus(sessionId, { mode: currentModeFor(sessionId, history), paused: null, error: null })
    runLoop(sessionId, loop)
  }

  function stop(sessionId) {
    cancelTrim(sessionId)
    loops.delete(sessionId)
    messageStore().abortBackfill(sessionId)
    islandOps.get(sessionId)?.controller.abort()
    islandPageMs.delete(sessionId)
    clearTimeout(jumpErrorTimers.get(sessionId))
    jumpErrorTimers.delete(sessionId)
    // Nothing is in flight any more: the spinner must not wait for the aborted requests to settle.
    if (statusBySession.value.has(sessionId)) setStatus(sessionId, { loading: false, jump: null, island: null })
  }

  function stopAll() {
    for (const sessionId of [...loops.keys()]) stop(sessionId)
  }

  /**
   * Requests one older page now (scroll toward the top): user priority. `urgent` = the user is already waiting at the top: that page keeps the small base
   * budget so it lands quickly; a prefetch (the user is still scrolling toward the edge) uses the
   * adaptive budget.
   */
  function requestOlder(sessionId, { urgent = true } = {}) {
    const history = messageStore().getHistoryState(sessionId)
    if (!history || !history.hasMoreBefore) return
    cancelTrim(sessionId) // the user is heading to the old edge: a pending trim waits (re-armed after the page lands)
    if (!loops.has(sessionId)) start(sessionId, { demand: 1, urgent })
    const loop = loops.get(sessionId)
    if (!loop) return
    if (loop.demand === 0) { loop.demand = 1; loop.urgent = urgent } else if (urgent) loop.urgent = true
  }

  // ---- island (issue #2030 stage 3 PR 3C-1) ----
  // sessionId -> { controller, kind: 'around' | 'before' | 'after' }: at most one island request.
  const islandOps = new Map()
  // sessionId -> last island page fetch duration (ms), for the scroll prefetch distance
  const islandPageMs = new Map()

  function endIslandOp(sessionId, op) {
    if (islandOps.get(sessionId) !== op) return
    islandOps.delete(sessionId)
    setStatus(sessionId, { jump: null, island: null })
  }

  const JUMP_ERROR_MS = 8000
  const jumpErrorTimers = new Map()

  function publishJumpError(sessionId, err, meta) {
    const what = meta?.label || 'that place'
    const text = err.reason === 'busy' ? `The server is busy — couldn't open ${what}, try again`
      : err.reason === 'not-found' ? `Couldn't find ${what} in the history`
      : err.reason === 'reset' ? 'The history changed — try again'
      : `Couldn't open ${what}: ${err.message || 'request failed'}`
    publishJumpErrorText(sessionId, text)
  }

  function publishJumpErrorText(sessionId, text) {
    setStatus(sessionId, { jumpError: text })
    clearTimeout(jumpErrorTimers.get(sessionId))
    jumpErrorTimers.set(sessionId, setTimeout(() => {
      jumpErrorTimers.delete(sessionId)
      if (statusBySession.value.has(sessionId)) setStatus(sessionId, { jumpError: null }) // never re-create a cleared status
    }, JUMP_ERROR_MS))
  }

  /**
   * Jump: ONE bounded window around `target` (`{ cursor }` | `{ messageId }` | `{ toolUseId }`)
   * becomes the island (replacing a previous one). Returns `{ promise, cancel }`; `promise`
   * resolves to the store's `{ status, anchorCursor, parentUnresolved }` and rejects with
   * `err.reason` 'not-found' | 'busy' | 'reset' | 'aborted' (cancelled) | 'superseded' | 'failed'.
   * `meta.label` is published on the status as `jump` (drives the spinner). A target already loaded
   * resolves at once ({ status: 'loaded' }). The suffix stays valid whatever happens.
   */
  function jumpAround(sessionId, target, meta = {}) {
    const store = messageStore()
    islandOps.get(sessionId)?.controller.abort()
    const op = { controller: new AbortController(), kind: 'around' }
    islandOps.set(sessionId, op)
    clearTimeout(jumpErrorTimers.get(sessionId))
    jumpErrorTimers.delete(sessionId)
    setStatus(sessionId, { jump: { ...meta }, island: null, error: null, jumpError: null })
    const promise = store.loadAround(sessionId, target, { signal: op.controller.signal })
    const settled = promise.then(
      (result) => {
        endIslandOp(sessionId, op)
        // A tool_use_id lookup whose launching message the server could not find: the window is
        // anchored on the tool_call record instead, so say so rather than landing silently.
        if (result?.parentUnresolved && meta.expectParent) {
          publishJumpErrorText(sessionId, `Couldn't find ${meta.label || 'the launching message'} in the history`)
        }
        return result
      },
      (err) => {
        endIslandOp(sessionId, op)
        // A failed jump is not silent (a cancel / supersede is the user's own doing, not a failure).
        if (err?.reason && !['aborted', 'superseded'].includes(err.reason)) publishJumpError(sessionId, err, meta)
        throw err
      },
    )
    settled.catch(() => {}) // callers handle it; never an unhandled rejection
    return { promise: settled, cancel: () => op.controller.abort() }
  }

  /** A short notice over the list (the jump-error slot: expires on its own). */
  function showNotice(sessionId, text) {
    publishJumpErrorText(sessionId, text)
  }

  /** Cancels the session's pending jump / island page (the loaded intervals stay valid). */
  function cancelJump(sessionId) {
    islandOps.get(sessionId)?.controller.abort()
  }

  /**
   * One island page on user demand: `before` (scrolling up past the island's top) or `after`
   * ("Load newer"; reaching the suffix merges the island into it). Applied once the scroll
   * gesture is idle (a prepend never lands mid-gesture unless the viewport is near the top).
   * Resolves true when a page was applied; false when there was nothing to load or the island
   * moved on. A failure is published as `island: { error }` on the status.
   */
  async function requestIslandPage(sessionId, edge) {
    const island = messageStore().getIsland(sessionId)
    if (!island || (edge === 'before' ? !island.hasMoreBefore : !island.hasMoreAfter)) return false
    const running = islandOps.get(sessionId)
    if (running) {
      // A jump is replacing the island: nothing to page. The same edge is already loading: share it.
      if (running.kind === 'around') return false
      if (running.kind === edge) return running.done
      // The other edge is loading (e.g. the scroll-up page when "Load newer" is pressed): take the turn after it.
      await running.done
      if (islandOps.has(sessionId)) return false
      return requestIslandPage(sessionId, edge)
    }
    cancelTrim(sessionId)
    const op = { controller: new AbortController(), kind: edge, done: null }
    islandOps.set(sessionId, op)
    op.done = runIslandPage(sessionId, edge, op)
    return op.done
  }

  async function runIslandPage(sessionId, edge, op) {
    const store = messageStore()
    setStatus(sessionId, { island: { loading: edge, error: null } })
    const startedAt = now()
    try {
      const page = await store.fetchIslandPage(sessionId, edge, { signal: op.controller.signal })
      if (!page || op.controller.signal.aborted) return false
      markPerf(`history-island-${edge}-fetch`, startedAt, now() - startedAt)
      islandPageMs.set(sessionId, now() - startedAt)
      if (edge === 'before') {
        for (;;) {
          const interaction = scrollInteraction.get(sessionId)
          if (!interaction?.gestureActive || interaction.nearTop) break
          if (op.controller.signal.aborted) return false
          await sleep(WAIT_POLL_MS)
        }
      }
      await idleSlot()
      if (op.controller.signal.aborted) return false
      const apply = () => store.applyIslandPage(sessionId, page)
      if (edge === 'before') return applyWithHook(sessionId, apply)
      // An `after` page past the budget releases the island's FAR (upper) end: rows above the viewport go.
      const island = store.getIsland(sessionId)
      const grown = (island?.bytes || 0) + (windowBytes(page.requestedFrom, page.endCursor) ?? 0)
      return grown > budgetBytes() ? applyWithHook(sessionId, apply, { trim: true }) : apply()
    } catch (err) {
      if (op.controller.signal.aborted || err?.reason === 'superseded' || err?.reason === 'aborted') return false
      pushDebugEvent('message', 'island-page-error', { sessionId, edge, message: err?.message })
      setStatus(sessionId, { island: { loading: null, error: err?.reason === 'reset' ? 'History changed' : (err?.message || 'Failed to load history') } })
      return false
    } finally {
      if (islandOps.get(sessionId) === op) {
        islandOps.delete(sessionId)
        const status = statusBySession.value.get(sessionId)
        if (status?.island && !status.island.error) setStatus(sessionId, { island: null })
        else if (status?.island?.loading) setStatus(sessionId, { island: { ...status.island, loading: null } })
      }
    }
  }

  const requestIslandOlder = (sessionId) => requestIslandPage(sessionId, 'before')
  const requestIslandNewer = (sessionId) => requestIslandPage(sessionId, 'after')

  /** Measured duration of the last page fetch of the interval the user is scrolling toward (ms; 0 unknown). */
  function getPageLatencyMs(sessionId, { island = false } = {}) {
    return (island ? islandPageMs.get(sessionId) : (loops.get(sessionId) ?? pageStats.get(sessionId))?.lastPageMs) || 0
  }

  /** 'auto' | 'manual' (slow link, nothing loads unrequested) for this session right now. */
  function getMode(sessionId) {
    const history = messageStore().getHistoryState(sessionId)
    return currentModeFor(sessionId, history)
  }

  /** Retry after the loop gave up on repeated errors. */
  function retry(sessionId) {
    stop(sessionId)
    start(sessionId, { demand: 1, urgent: true }) // the user is waiting for that page: the budget does not refuse it
  }

  /** The message list registers its hook around an older page being applied. */
  function setPageApplyHook(sessionId, hook) {
    pageApplyHooks.set(sessionId, hook)
  }

  /** Removes `hook` — only if it is still the registered one (another list instance may have replaced it). */
  function clearPageApplyHook(sessionId, hook) {
    if (pageApplyHooks.get(sessionId) === hook) pageApplyHooks.delete(sessionId)
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
    jumpAround,
    cancelJump,
    showNotice,
    requestIslandOlder,
    requestIslandNewer,
    getMode,
    getPageLatencyMs,
    retry,
    setScrollInteraction,
    setPageApplyHook,
    clearPageApplyHook,
    setTrimGuard,
    clearTrimGuard,
    requestTrimCheck,
    onSessionSwitch,
    isRunning: (sessionId) => loops.has(sessionId),
  }
}

export const historyBackfill = createHistoryBackfill()

/**
 * Schedules the history index of `sessionId` when only a suffix of its history is loaded (after
 * `after`, the snapshot requests that must settle first — issue #2030 stage 2). Older pages load
 * only on demand.
 */
export function scheduleIndexIfIncomplete(sessionId, after = []) {
  if (useMessageStore().isHistoryComplete(sessionId)) return
  historyBackfill.scheduleIndex(sessionId, after)
}

export function useHistoryBackfill() {
  return historyBackfill
}
