import { describe, it, expect, vi } from 'vitest'
import {
  createHistoryBackfill, detectSlowLink,
  QUIET_MS, CATCHUP_EVENT_COUNT, SLOW_LINK_BPS, PAGE_BUDGET_BYTES, MIN_PAGE_BUDGET_BYTES, MAX_PAGE_BUDGET_BYTES,
  MAX_PAGE_RECORDS, nextPageBudget, APPLY_OK_MS, APPLY_SLOW_MS,
} from '../useHistoryBackfill'

// Issue #2030 (stage 1b) / #2331: on-demand history page scheduler. Time is virtual: every sleep()
// advances the clock instantly, so ordering and gaps are asserted without real timers.

const SID = 'sess-1'

function setup({ pages = 5, pageMs = 100, history = {}, polling = {}, hidden = () => false, slow = () => false, mutations = () => 0, onSleep } = {}) {
  let clock = 1_000_000
  const state = { remaining: pages, applied: [], fetched: [], aborts: [], restarts: [], sleeps: [] }
  const pollingStore = { currentSessionId: SID, sessionState: 'connected', getLastBatchInfo: () => null, ...polling }
  const historyState = { hasMoreBefore: true, startCursor: 'b:100', baseUrl: '/x', source: 'load', generation: 1, ...history }
  const messageStore = {
    getHistoryState: () => (state.remaining > 0 && historyState.hasMoreBefore ? historyState : { ...historyState, hasMoreBefore: false }),
    claimBackfill: () => { const c = new AbortController(); state.aborts.push(c); return c },
    releaseBackfill: () => {},
    abortBackfill: () => state.aborts.forEach(c => c.abort()),
    fetchOlderPage: vi.fn(async (_sid, opts) => {
      state.fetched.push({ at: clock, opts })
      clock += pageMs
      if (state.failNext) { state.failNext = false; const e = new Error('reset'); e.isHistoryReset = true; throw e }
      return { records: [], hasMoreBefore: state.remaining > 1 }
    }),
    applyFetchedOlderPage: vi.fn(() => { state.remaining--; state.applied.push(clock); return true }),
    loadMessages: vi.fn(async (...args) => { state.restarts.push(args) }),
    isHistoryComplete: () => !(state.remaining > 0 && historyState.hasMoreBefore),
    // Turn jump (stage 2): the target message is "loaded" once `state.loadedAfter` pages were applied.
    isMessageLoaded: vi.fn(() => state.loadedAfter != null && state.applied.length >= state.loadedAfter),
    ensureHistoryIndex: vi.fn(async () => null),
  }
  const scheduler = createHistoryBackfill({
    messageStore: () => messageStore,
    pollingStore: () => pollingStore,
    now: () => clock,
    // A real macrotask per sleep: a loop that only yields microtasks would starve settle()'s timers.
    sleep: async (ms) => { state.sleeps.push(ms); clock += ms; onSleep?.(state, ms); await new Promise(r => setTimeout(r, 0)) },
    idleSlot: async () => {},
    mutationsInFlight: mutations,
    isHidden: hidden,
    isSlowLink: slow,
  })
  return { scheduler, state, messageStore, pollingStore, historyState, clock: () => clock, advance: (ms) => { clock += ms } }
}

async function settle(scheduler, ticks = 200) {
  for (let i = 0; i < ticks && scheduler.isRunning(SID); i++) await new Promise(r => setTimeout(r, 1))
}

// Chained demand: request one older page at a time until the history is exhausted (what a user
// scrolling to the top does). `urgent: false` = scroll prefetch (adaptive page budget).
async function drain(t, { urgent = true, max = 1000 } = {}) {
  for (let i = 0; i < max && t.state.remaining > 0; i++) {
    t.scheduler.requestOlder(SID, { urgent })
    await settle(t.scheduler, 400)
    if (t.state.applied.length === i) break
  }
}

describe('history page scheduler (issue #2331: on-demand only)', () => {
  it('fetches nothing without a demand and keeps no timers while idle', async () => {
    vi.useFakeTimers()
    try {
      const t = setup({ pages: 5 })
      t.scheduler.start(SID)
      await vi.advanceTimersByTimeAsync(60000)
      expect(t.state.fetched).toHaveLength(0)
      expect(t.state.sleeps).toHaveLength(0)
      expect(vi.getTimerCount()).toBe(0)
      expect(t.scheduler.isRunning(SID)).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('requestOlder loads exactly one page and then the loop ends', async () => {
    const t = setup({ pages: 5 })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
    expect(t.scheduler.isRunning(SID)).toBe(false)
    await new Promise(r => setTimeout(r, 30))
    expect(t.state.fetched).toHaveLength(1)
    expect(t.scheduler.statusBySession.value.get(SID).loading).toBe(false)
  })

  it('chained demands reach the end of the history and clear the status', async () => {
    const t = setup({ pages: 60, pageMs: 10 })
    await drain(t)
    expect(t.state.applied).toHaveLength(60)
    expect(t.scheduler.isRunning(SID)).toBe(false)
    expect(t.scheduler.statusBySession.value.has(SID)).toBe(false)
  })

  it('publishes loading while a page is in flight and clears it afterwards', async () => {
    const t = setup({ pages: 2 })
    const seen = []
    const real = t.messageStore.fetchOlderPage.getMockImplementation()
    t.messageStore.fetchOlderPage.mockImplementation(async (...a) => { seen.push(t.scheduler.statusBySession.value.get(SID).loading); return real(...a) })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(seen).toEqual([true])
    expect(t.scheduler.statusBySession.value.get(SID).loading).toBe(false)
  })

  it('stop() clears loading / jump / island at once, without waiting for the aborted request', async () => {
    const t = setup({ pages: 3 })
    t.messageStore.fetchOlderPage.mockImplementation(() => new Promise(() => {})) // never settles
    t.scheduler.requestOlder(SID)
    await new Promise(r => setTimeout(r, 5))
    expect(t.scheduler.statusBySession.value.get(SID).loading).toBe(true)
    t.scheduler.stop(SID)
    expect(t.scheduler.statusBySession.value.get(SID).loading).toBe(false)
  })

  it('starts at the base page budget and asks for the server\'s record cap', async () => {
    const t = setup({ pages: 2 })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.fetched[0].opts.maxBytes).toBe(PAGE_BUDGET_BYTES)
    expect(t.state.fetched[0].opts.maxRecords).toBe(MAX_PAGE_RECORDS)
  })

  it('doubles the page budget while prefetch pages are fast, up to the server cap (the estimate outlives each loop)', async () => {
    const t = setup({ pages: 12, pageMs: 50 })
    await drain(t, { urgent: false })
    const budgets = t.state.fetched.map(f => f.opts.maxBytes)
    expect(budgets.slice(0, 5)).toEqual([256, 512, 1024, 2048, 4096].map(k => k * 1024))
    expect(Math.max(...budgets)).toBe(MAX_PAGE_BUDGET_BYTES)
    expect(budgets.at(-1)).toBe(MAX_PAGE_BUDGET_BYTES)
  })

  it('halves the page budget when pages are slow, down to the minimum, and keeps it for mid-speed pages', async () => {
    const slow = setup({ pages: 6, pageMs: 3000 })
    await drain(slow, { urgent: false })
    // a prefetch never goes below the base budget (a smaller page only costs another round trip)
    expect(new Set(slow.state.fetched.map(f => f.opts.maxBytes))).toEqual(new Set([PAGE_BUDGET_BYTES]))
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 3000)).toBe(PAGE_BUDGET_BYTES / 2)
    expect(nextPageBudget(MIN_PAGE_BUDGET_BYTES, 3000)).toBe(MIN_PAGE_BUDGET_BYTES)
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 1000)).toBe(PAGE_BUDGET_BYTES)

    const mid = setup({ pages: 4, pageMs: 1000 })
    await drain(mid, { urgent: false })
    expect(new Set(mid.state.fetched.map(f => f.opts.maxBytes))).toEqual(new Set([PAGE_BUDGET_BYTES]))
  })

  it('does not grow the budget when applying a page is expensive, and shrinks it when very expensive', () => {
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100, APPLY_OK_MS + 1)).toBe(PAGE_BUDGET_BYTES)
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100, APPLY_SLOW_MS + 1)).toBe(PAGE_BUDGET_BYTES / 2)
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100, APPLY_OK_MS)).toBe(PAGE_BUDGET_BYTES * 2)
  })

  it('a failed page shrinks the budget', async () => {
    const e = setup({ pages: 4, pageMs: 50 })
    let calls = 0
    const real = e.messageStore.fetchOlderPage.getMockImplementation()
    e.messageStore.fetchOlderPage.mockImplementation(async (...a) => { calls++; if (calls === 3) throw new Error('timeout'); return real(...a) })
    await drain(e, { urgent: false })
    const sizes = e.state.fetched.map(f => f.opts.maxBytes)
    // 256K -> 512K -> (3rd call fails, no fetched entry) -> retried at half of the grown 1M budget
    expect(sizes[0]).toBe(PAGE_BUDGET_BYTES)
    expect(sizes[1]).toBe(PAGE_BUDGET_BYTES * 2)
    expect(sizes[2]).toBe(PAGE_BUDGET_BYTES * 2) // 1 MiB halved after the failure
  })

  it('an urgent page always uses the base budget even after the budget has grown', async () => {
    const t = setup({ pages: 8, pageMs: 50, slow: () => true })
    for (let i = 0; i < 5; i++) { t.scheduler.requestOlder(SID); await settle(t.scheduler, 30) }
    expect(t.state.fetched.length).toBeGreaterThanOrEqual(3)
    expect(new Set(t.state.fetched.map(f => f.opts.maxBytes))).toEqual(new Set([PAGE_BUDGET_BYTES]))
  })

  it('a prefetch (not urgent) uses the adaptive budget, an urgent request the base budget (stage 3 PR 3D)', async () => {
    const t = setup({ pages: 8, pageMs: 50, slow: () => true })
    for (let i = 0; i < 4; i++) { t.scheduler.requestOlder(SID, { urgent: false }); await settle(t.scheduler, 30) }
    const budgets = t.state.fetched.map(f => f.opts.maxBytes)
    expect(budgets[0]).toBe(PAGE_BUDGET_BYTES)
    expect(Math.max(...budgets)).toBeGreaterThan(PAGE_BUDGET_BYTES) // fast pages grew the budget
    const before = t.state.fetched.length
    t.scheduler.requestOlder(SID, { urgent: true })
    await settle(t.scheduler, 30)
    expect(t.state.fetched[before].opts.maxBytes).toBe(PAGE_BUDGET_BYTES)
  })

  it('a prefetch never uses less than the base budget even when pages are slow', async () => {
    const t = setup({ pages: 6, pageMs: 3000, slow: () => true })
    for (let i = 0; i < 4; i++) { t.scheduler.requestOlder(SID, { urgent: false }); await settle(t.scheduler, 30) }
    expect(t.state.fetched.length).toBeGreaterThanOrEqual(3)
    expect(Math.min(...t.state.fetched.map(f => f.opts.maxBytes))).toBe(PAGE_BUDGET_BYTES)
  })

  it('runs the view hook right before an older page is applied and its returned function right after; a failing hook never fails the page (stage 3 PR 3D)', async () => {
    const t = setup({ pages: 2 })
    const order = []
    t.messageStore.applyFetchedOlderPage = vi.fn(() => { order.push('apply'); t.state.remaining--; t.state.applied.push(1); return true })
    t.scheduler.setPageApplyHook(SID, () => { order.push('before'); return () => order.push('after') })
    await drain(t)
    expect(order).toEqual(['before', 'apply', 'after', 'before', 'apply', 'after'])
    const t2 = setup({ pages: 1 })
    const failing = () => { throw new Error('boom') }
    t2.scheduler.setPageApplyHook(SID, failing)
    await drain(t2)
    expect(t2.state.applied).toHaveLength(1)
    t2.scheduler.clearPageApplyHook(SID, failing)
  })

  it('clearPageApplyHook removes a hook only while it is still the registered one', async () => {
    const t = setup({ pages: 2 })
    const calls = []
    const mine = () => { calls.push('mine') }
    const theirs = () => { calls.push('theirs') }
    t.scheduler.setPageApplyHook(SID, mine)
    t.scheduler.setPageApplyHook(SID, theirs) // another list instance replaced it
    t.scheduler.clearPageApplyHook(SID, mine) // the first instance unmounting must not remove the replacement
    await drain(t)
    expect(calls).toEqual(['theirs', 'theirs'])
  })

  it('showNotice publishes a short notice that expires on its own (stage 3 PR 3F)', async () => {
    vi.useFakeTimers()
    const t = setup({ pages: 1, slow: () => true })
    t.scheduler.showNotice(SID, "Couldn't find that message in the history")
    expect(t.scheduler.statusBySession.value.get(SID).jumpError).toBe("Couldn't find that message in the history")
    await vi.advanceTimersByTimeAsync(8100)
    expect(t.scheduler.statusBySession.value.get(SID).jumpError).toBeNull()
    vi.useRealTimers()
  })

  it('pure sizing function', () => {
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100)).toBe(2 * PAGE_BUDGET_BYTES)
    expect(nextPageBudget(MAX_PAGE_BUDGET_BYTES, 100)).toBe(MAX_PAGE_BUDGET_BYTES)
    expect(nextPageBudget(MIN_PAGE_BUDGET_BYTES, 9999)).toBe(MIN_PAGE_BUDGET_BYTES)
  })

  it('does not fetch while the tab is hidden and resumes when visible', async () => {
    let hidden = true
    const t = setup({ pages: 2, hidden: () => hidden, onSleep: (state) => { if (state.sleeps.length === 5) hidden = false } })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(5)
    expect(t.state.fetched[0].at).toBeGreaterThan(1_000_000) // nothing fetched before it became visible
    expect(t.state.applied).toHaveLength(1)
  })

  it.each(['connecting', 'resyncing', 'backoff', 'unreachable'])('pauses while the connection is %s', async (connState) => {
    const t = setup({ pages: 1, polling: { sessionState: connState }, onSleep: (state) => { if (state.sleeps.length === 3) t.pollingStore.sessionState = 'connected' } })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(3)
    expect(t.state.applied).toHaveLength(1)
  })

  it('pauses while the last poll batch was a catch-up burst', async () => {
    const t = setup({ pages: 1 })
    const burstAt = t.clock()
    t.pollingStore.getLastBatchInfo = () => ({ at: burstAt, count: CATCHUP_EVENT_COUNT })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler, 5)
    expect(t.state.fetched).toHaveLength(0) // still inside QUIET_MS
    t.advance(QUIET_MS + 1)
    t.pollingStore.getLastBatchInfo = () => null
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
  })

  it('a small live batch does not hold a requested page back', async () => {
    const t = setup({ pages: 1 })
    t.pollingStore.getLastBatchInfo = () => ({ at: t.clock(), count: 1 })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
  })

  it('pauses while a user REST action is in flight', async () => {
    let inFlight = 1
    const t = setup({ pages: 1, mutations: () => inFlight, onSleep: (state) => { if (state.sleeps.length === 3) inFlight = 0 } })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(3)
    expect(t.state.applied).toHaveLength(1)
  })

  it('reports the measured page latency of the suffix pages and of the island, also between loops', async () => {
    const t = setup({ pages: 2, pageMs: 300, slow: () => true })
    expect(t.scheduler.getPageLatencyMs(SID)).toBe(0)
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.scheduler.isRunning(SID)).toBe(false)
    expect(t.scheduler.getPageLatencyMs(SID)).toBeGreaterThanOrEqual(300)
    t.messageStore.getIsland = () => ({ hasMoreBefore: true, hasMoreAfter: false })
    t.messageStore.fetchIslandPage = vi.fn(async () => { t.advance(120); return { edge: 'before', records: [] } })
    t.messageStore.applyIslandPage = vi.fn(() => true)
    await t.scheduler.requestIslandOlder(SID)
    expect(t.scheduler.getPageLatencyMs(SID, { island: true })).toBe(120)
  })

  it('never throttles an archive view on a live connection state', async () => {
    const t = setup({ pages: 1, history: { source: 'archive' }, polling: { sessionState: 'archived' } })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
  })

  it('stops (and aborts the in-flight page) when the session is no longer current', async () => {
    const t = setup({ pages: 50 })
    t.pollingStore.sessionState = 'connecting' // hold the request so the session can change under it
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler, 3)
    t.pollingStore.currentSessionId = 'other'
    await settle(t.scheduler)
    expect(t.scheduler.isRunning(SID)).toBe(false)
    expect(t.state.applied).toHaveLength(0)
    t.scheduler.stop(SID)
    expect(t.state.aborts.every(c => c.signal.aborted)).toBe(true)
  })

  it('keeps a single loop per session (rapid requests share it)', async () => {
    const t = setup({ pages: 3 })
    t.scheduler.requestOlder(SID)
    t.scheduler.requestOlder(SID)
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
    expect(t.messageStore.fetchOlderPage).toHaveBeenCalledTimes(1)
  })

  it('restarts from the tail when the server says the cursor/history is no longer valid', async () => {
    const t = setup({ pages: 2 })
    t.state.failNext = true
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.restarts).toHaveLength(1)
    expect(t.state.restarts[0][3]).toMatchObject({ window: 'tail', baseUrl: '/x' })
    expect(t.state.applied).toHaveLength(1) // the requested page still lands after the restart
  })

  it('a failing tail restart counts as an ordinary failure (no unhandled rejection) and an archive restart owns its signal', async () => {
    const t = setup({ pages: 2, history: { source: 'archive' }, polling: { sessionState: 'archived' } })
    t.state.failNext = true
    t.messageStore.loadMessages.mockRejectedValueOnce(new Error('offline'))
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.messageStore.loadMessages).toHaveBeenCalledTimes(1)
    expect(t.messageStore.loadMessages.mock.calls[0][3].signal).toBeInstanceOf(AbortSignal) // archive views never claim the live tracker
    expect(t.state.applied).toHaveLength(1) // recovered after the counted failure
  })

  it('holds an applied page back during a scroll gesture unless the viewport is near the top', async () => {
    const t = setup({ pages: 1, onSleep: (state) => { if (state.sleeps.length === 4) t.scheduler.setScrollInteraction(SID, { gestureActive: false, nearTop: false }) } })
    t.scheduler.setScrollInteraction(SID, { gestureActive: true, nearTop: false })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(4)
    expect(t.state.applied).toHaveLength(1)

    const nearTop = setup({ pages: 1 })
    nearTop.scheduler.setScrollInteraction(SID, { gestureActive: true, nearTop: true })
    nearTop.scheduler.requestOlder(SID)
    await settle(nearTop.scheduler)
    expect(nearTop.state.applied).toHaveLength(1)
  })

  describe('slow-link mode', () => {
    it('reports manual mode but still loads exactly one page per request', async () => {
      const t = setup({ pages: 5, slow: () => true })
      t.scheduler.start(SID)
      await settle(t.scheduler, 30)
      expect(t.state.fetched).toHaveLength(0)
      expect(t.scheduler.getMode(SID)).toBe('manual')
      t.scheduler.requestOlder(SID)
      await settle(t.scheduler, 30)
      expect(t.state.applied).toHaveLength(1)
      t.scheduler.requestOlder(SID)
      await settle(t.scheduler, 30)
      expect(t.state.applied).toHaveLength(2)
    })
  })

  it('a requested page does not bypass the catch-up pause', async () => {
    const t = setup({ pages: 2, slow: () => true })
    const burstAt = t.clock()
    t.pollingStore.getLastBatchInfo = () => ({ at: burstAt, count: CATCHUP_EVENT_COUNT })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler, 5)
    expect(t.state.fetched).toHaveLength(0)
    t.pollingStore.getLastBatchInfo = () => null
    await settle(t.scheduler, 30)
    expect(t.state.applied).toHaveLength(1)
  })

  it('gives up with an error status after repeated failures and Retry resumes', async () => {
    const t = setup({ pages: 1 })
    t.messageStore.fetchOlderPage.mockRejectedValue(new Error('boom'))
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler, 500)
    const status = t.scheduler.statusBySession.value.get(SID)
    expect(status.error).toBe('boom')
    expect(status.loading).toBe(false)
    expect(t.scheduler.isRunning(SID)).toBe(false)
    t.messageStore.fetchOlderPage.mockImplementation(async () => ({ records: [], hasMoreBefore: false }))
    t.scheduler.retry(SID)
    await settle(t.scheduler, 200)
    expect(t.state.applied).toHaveLength(1)
    expect(t.scheduler.statusBySession.value.get(SID)?.error ?? null).toBeNull()
  })
})

describe('jumpAround / island pages (issue #2030 stage 3 PR 3C-1)', () => {
  // The island lives in the message store (loadAround / fetchIslandPage / applyIslandPage there);
  // the scheduler only drives it, publishes the status and keeps the cancel semantics.
  function withIsland(t, { island = { hasMoreBefore: true, hasMoreAfter: true } } = {}) {
    const calls = { loadAround: [], fetch: [], apply: [] }
    t.messageStore.loadAround = vi.fn((sid, target, opts) => {
      calls.loadAround.push({ sid, target, opts })
      return new Promise((resolve, reject) => {
        calls.resolve = () => resolve({ status: 'island' })
        opts.signal.addEventListener('abort', () => reject(Object.assign(new Error('Jump cancelled'), { reason: 'aborted' })))
        calls.reject = (reason) => reject(Object.assign(new Error(reason), { reason }))
      })
    })
    t.messageStore.getIsland = () => island
    t.messageStore.fetchIslandPage = vi.fn(async (sid, edge) => { calls.fetch.push(edge); return { edge, records: [] } })
    t.messageStore.applyIslandPage = vi.fn((sid, page) => { calls.apply.push(page.edge); return true })
    return calls
  }

  it('publishes the jump on the status while the window loads and clears it when it lands', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t)
    const jump = t.scheduler.jumpAround(SID, { cursor: 'b:5' }, { label: 'turn 4' })
    expect(calls.loadAround[0].target).toEqual({ cursor: 'b:5' })
    expect(t.scheduler.statusBySession.value.get(SID).jump).toMatchObject({ label: 'turn 4' })
    calls.resolve()
    await expect(jump.promise).resolves.toEqual({ status: 'island' })
    expect(t.scheduler.statusBySession.value.get(SID)?.jump ?? null).toBeNull()
  })

  it('a failed jump (busy / not-found / network) is published on the status for the banner; a cancel or supersede is not', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t)
    const busy = t.scheduler.jumpAround(SID, { cursor: 'b:5' }, { label: 'turn 4' })
    calls.reject('busy')
    await expect(busy.promise).rejects.toMatchObject({ reason: 'busy' })
    expect(t.scheduler.statusBySession.value.get(SID).jumpError).toMatch(/busy.*turn 4/)
    expect(t.scheduler.statusBySession.value.get(SID).jump ?? null).toBeNull()
    // the next jump clears it; a cancelled one publishes nothing
    const next = t.scheduler.jumpAround(SID, { cursor: 'b:6' })
    expect(t.scheduler.statusBySession.value.get(SID).jumpError).toBeNull()
    next.cancel()
    await expect(next.promise).rejects.toMatchObject({ reason: 'aborted' })
    expect(t.scheduler.statusBySession.value.get(SID).jumpError ?? null).toBeNull()
    const gone = t.scheduler.jumpAround(SID, { messageId: 'x' }, { label: 'search result' })
    calls.reject('not-found')
    await expect(gone.promise).rejects.toMatchObject({ reason: 'not-found' })
    expect(t.scheduler.statusBySession.value.get(SID).jumpError).toMatch(/search result/)
  })

  it('the published jump error expires on its own and never re-creates a cleared status', async () => {
    vi.useFakeTimers()
    try {
      const t = setup({ pages: 3, slow: () => true })
      const calls = withIsland(t)
      const failed = t.scheduler.jumpAround(SID, { cursor: 'b:5' }, { label: 'turn 4' })
      calls.reject('failed')
      await expect(failed.promise).rejects.toMatchObject({ reason: 'failed' })
      expect(t.scheduler.statusBySession.value.get(SID).jumpError).toBeTruthy()
      await vi.advanceTimersByTimeAsync(8100)
      expect(t.scheduler.statusBySession.value.get(SID).jumpError).toBeNull()
      // stop() drops a pending expiry: no status is resurrected later
      const again = t.scheduler.jumpAround(SID, { cursor: 'b:6' }, { label: 'turn 5' })
      calls.reject('busy')
      await expect(again.promise).rejects.toMatchObject({ reason: 'busy' })
      t.scheduler.stop(SID)
      t.scheduler.statusBySession.value = new Map()
      await vi.advanceTimersByTimeAsync(8100)
      expect(t.scheduler.statusBySession.value.has(SID)).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('cancel and cancelJump (the banner button) abort the request: reason "aborted", status cleared', async () => {
    const t = setup({ pages: 3, slow: () => true })
    withIsland(t)
    const first = t.scheduler.jumpAround(SID, { cursor: 'b:5' })
    first.cancel()
    await expect(first.promise).rejects.toMatchObject({ reason: 'aborted' })
    const second = t.scheduler.jumpAround(SID, { messageId: 'm1' })
    t.scheduler.cancelJump(SID)
    await expect(second.promise).rejects.toMatchObject({ reason: 'aborted' })
    expect(t.scheduler.statusBySession.value.get(SID)?.jump ?? null).toBeNull()
  })

  it('a second jump aborts the first request', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t)
    const first = t.scheduler.jumpAround(SID, { cursor: 'b:5' })
    const second = t.scheduler.jumpAround(SID, { cursor: 'b:9' })
    await expect(first.promise).rejects.toMatchObject({ reason: 'aborted' })
    expect(calls.loadAround[0].opts.signal.aborted).toBe(true)
    calls.resolve()
    await expect(second.promise).resolves.toBeTruthy()
  })

  it('the spinner state never sticks: a superseded jump hands over to the new one, which then clears it; stop() (session switch) clears at once', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t)
    const status = () => t.scheduler.statusBySession.value.get(SID)
    const first = t.scheduler.jumpAround(SID, { cursor: 'b:5' }, { label: 'one' })
    const second = t.scheduler.jumpAround(SID, { cursor: 'b:9' }, { label: 'two' })
    await expect(first.promise).rejects.toMatchObject({ reason: 'aborted' })
    expect(status().jump).toMatchObject({ label: 'two' }) // the aborted first must not clear the second's
    calls.resolve()
    await second.promise
    expect(status()?.jump ?? null).toBeNull()
    // an island page in flight and a jump in flight are both cleared by stop() without waiting for the requests
    t.messageStore.fetchIslandPage = vi.fn(() => new Promise(() => {}))
    void t.scheduler.requestIslandOlder(SID)
    await new Promise(r => setTimeout(r, 5))
    expect(status().island).toMatchObject({ loading: 'before' })
    t.scheduler.stop(SID)
    expect(status().island ?? null).toBeNull()
    const third = t.scheduler.jumpAround(SID, { cursor: 'b:7' }, { label: 'three' })
    t.scheduler.stop(SID)
    expect(status().jump ?? null).toBeNull()
    await expect(third.promise).rejects.toMatchObject({ reason: 'aborted' })
  })

  it('works in slow-link (manual) mode and needs no backfill loop', async () => {
    const t = setup({ pages: 6, slow: () => true })
    const calls = withIsland(t)
    const jump = t.scheduler.jumpAround(SID, { cursor: 'b:5' })
    calls.resolve()
    await jump.promise
    expect(t.state.fetched).toHaveLength(0)
    expect(t.scheduler.isRunning(SID)).toBe(false)
  })

  it('requestIslandOlder / requestIslandNewer fetch and apply one page each on demand', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t)
    expect(await t.scheduler.requestIslandOlder(SID)).toBe(true)
    expect(await t.scheduler.requestIslandNewer(SID)).toBe(true)
    expect(calls.fetch).toEqual(['before', 'after'])
    expect(calls.apply).toEqual(['before', 'after'])
  })

  it('does nothing for an edge that has nothing more, without an island, or while another island request runs', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t, { island: { hasMoreBefore: false, hasMoreAfter: true } })
    expect(await t.scheduler.requestIslandOlder(SID)).toBe(false)
    t.messageStore.getIsland = () => null
    expect(await t.scheduler.requestIslandNewer(SID)).toBe(false)
    t.messageStore.getIsland = () => ({ hasMoreBefore: true, hasMoreAfter: true })
    const jump = t.scheduler.jumpAround(SID, { cursor: 'b:1' })
    expect(await t.scheduler.requestIslandNewer(SID)).toBe(false) // an island request is already running
    calls.resolve()
    await jump.promise
    expect(calls.fetch).toEqual([])
  })

  it('"Load newer" pressed while the scroll-up page loads waits for it and then loads its own page; the same edge is shared, never doubled', async () => {
    const t = setup({ pages: 3, slow: () => true })
    const calls = withIsland(t)
    let release
    t.messageStore.fetchIslandPage = vi.fn((sid, edge) => new Promise((resolve) => {
      calls.fetch.push(edge)
      if (edge === 'before') release = () => resolve({ edge, records: [] })
      else resolve({ edge, records: [] })
    }))
    const older = t.scheduler.requestIslandOlder(SID)
    const olderAgain = t.scheduler.requestIslandOlder(SID) // the scroll handler fires again: shares the running request
    const newer = t.scheduler.requestIslandNewer(SID)       // the button: takes its turn after the running page
    await new Promise(r => setTimeout(r, 20))
    expect(calls.fetch).toEqual(['before']) // one request at a time
    release()
    expect(await older).toBe(true)
    expect(await olderAgain).toBe(true)
    expect(await newer).toBe(true)
    expect(calls.fetch).toEqual(['before', 'after'])
    expect(calls.apply).toEqual(['before', 'after'])
  })

  it('a failing island page publishes the error on the status and does not throw', async () => {
    const t = setup({ pages: 3, slow: () => true })
    withIsland(t)
    t.messageStore.fetchIslandPage = vi.fn(async () => { throw Object.assign(new Error('boom'), { reason: 'failed' }) })
    expect(await t.scheduler.requestIslandNewer(SID)).toBe(false)
    expect(t.scheduler.statusBySession.value.get(SID).island).toMatchObject({ error: 'boom' })
    // a retry that works clears it
    t.messageStore.fetchIslandPage = vi.fn(async (sid, edge) => ({ edge, records: [] }))
    expect(await t.scheduler.requestIslandNewer(SID)).toBe(true)
    expect(t.scheduler.statusBySession.value.get(SID)?.island ?? null).toBeNull()
  })

  it('scheduleIndex fetches the index after the snapshots settle, but not on a slow link or when complete', async () => {
    const t = setup({ pages: 3 })
    let release
    const snapshot = new Promise(r => { release = r })
    const pending = t.scheduler.scheduleIndex(SID, [snapshot, Promise.reject(new Error('snapshot failed'))])
    await new Promise(r => setTimeout(r, 5))
    expect(t.messageStore.ensureHistoryIndex).not.toHaveBeenCalled()
    release()
    await pending
    expect(t.messageStore.ensureHistoryIndex).toHaveBeenCalledWith(SID)

    const slow = setup({ pages: 3, slow: () => true })
    await slow.scheduler.scheduleIndex(SID)
    expect(slow.messageStore.ensureHistoryIndex).not.toHaveBeenCalled()

    const done = setup({ pages: 0 })
    await done.scheduler.scheduleIndex(SID)
    expect(done.messageStore.ensureHistoryIndex).not.toHaveBeenCalled()
  })
})

describe('detectSlowLink', () => {
  it('honors the Network Information API when present', () => {
    expect(detectSlowLink({}, { connection: { saveData: true } })).toBe(true)
    expect(detectSlowLink({}, { connection: { effectiveType: '2g' } })).toBe(true)
    expect(detectSlowLink({}, { connection: { effectiveType: 'slow-2g' } })).toBe(true)
    expect(detectSlowLink({ windowBps: 1 }, { connection: { effectiveType: '4g' } })).toBe(false)
  })

  it('falls back to first-window throughput when the API is absent (e.g. Safari)', () => {
    expect(detectSlowLink({ windowBps: SLOW_LINK_BPS - 1 }, {})).toBe(true)
    expect(detectSlowLink({ windowBps: SLOW_LINK_BPS + 1 }, {})).toBe(false)
    expect(detectSlowLink({ windowBps: null }, {})).toBe(false)
  })
})
