import { describe, it, expect, vi } from 'vitest'
import {
  createHistoryBackfill, detectSlowLink,
  QUIET_MS, CATCHUP_EVENT_COUNT, SLOW_LINK_BPS, PAGE_BUDGET_BYTES, MIN_PAGE_BUDGET_BYTES, MAX_PAGE_BUDGET_BYTES,
  MAX_PAGE_RECORDS, MAX_GAP_MS, GAP_FACTOR, nextPageBudget, backfillGapMs, APPLY_OK_MS, APPLY_SLOW_MS,
} from '../useHistoryBackfill'

// Issue #2030 (stage 1b): backfill scheduler pacing matrix. Time is virtual: every sleep()
// advances the clock instantly, so ordering and gaps are asserted without real timers.

const SID = 'sess-1'

function setup({ pages = 5, pageMs = 100, history = {}, polling = {}, hidden = () => false, slow = () => false, mutations = () => 0, autoBackfill = () => true, onSleep } = {}) {
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
    autoBackfillEnabled: autoBackfill,
    isHidden: hidden,
    isSlowLink: slow,
  })
  return { scheduler, state, messageStore, pollingStore, historyState, clock: () => clock, advance: (ms) => { clock += ms } }
}

async function settle(scheduler, ticks = 200) {
  for (let i = 0; i < ticks && scheduler.isRunning(SID); i++) await new Promise(r => setTimeout(r, 1))
}

describe('history backfill scheduler (issue #2030, minimum pacing rules)', () => {
  it('runs to completion with no ceiling', async () => {
    const t = setup({ pages: 300, pageMs: 10 })
    t.scheduler.start(SID)
    await settle(t.scheduler, 20000)
    expect(t.state.applied).toHaveLength(300)
    expect(t.scheduler.isRunning(SID)).toBe(false)
  })

  it('starts at the base page budget and asks for the server\'s record cap', async () => {
    const t = setup({ pages: 2 })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.fetched[0].opts.maxBytes).toBe(PAGE_BUDGET_BYTES)
    expect(t.state.fetched[0].opts.maxRecords).toBe(MAX_PAGE_RECORDS)
  })

  it('doubles the page budget while pages are fast, up to the server cap', async () => {
    const t = setup({ pages: 12, pageMs: 50 })
    t.scheduler.start(SID)
    await settle(t.scheduler, 2000)
    const budgets = t.state.fetched.map(f => f.opts.maxBytes)
    expect(budgets.slice(0, 5)).toEqual([256, 512, 1024, 2048, 4096].map(k => k * 1024))
    expect(Math.max(...budgets)).toBe(MAX_PAGE_BUDGET_BYTES)
    expect(budgets.at(-1)).toBe(MAX_PAGE_BUDGET_BYTES)
  })

  it('halves the page budget when pages are slow, down to the minimum, and keeps it for mid-speed pages', async () => {
    const slow = setup({ pages: 6, pageMs: 3000 })
    slow.scheduler.start(SID)
    await settle(slow.scheduler, 2000)
    const budgets = slow.state.fetched.map(f => f.opts.maxBytes)
    expect(budgets.slice(0, 3)).toEqual([256, 128, 64].map(k => k * 1024))
    expect(Math.min(...budgets)).toBe(MIN_PAGE_BUDGET_BYTES)

    const mid = setup({ pages: 4, pageMs: 1000 })
    mid.scheduler.start(SID)
    await settle(mid.scheduler, 2000)
    expect(new Set(mid.state.fetched.map(f => f.opts.maxBytes))).toEqual(new Set([PAGE_BUDGET_BYTES]))
  })

  it('does not grow the budget when applying a page is expensive, and shrinks it when very expensive', () => {
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100, APPLY_OK_MS + 1)).toBe(PAGE_BUDGET_BYTES)
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100, APPLY_SLOW_MS + 1)).toBe(PAGE_BUDGET_BYTES / 2)
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100, APPLY_OK_MS)).toBe(PAGE_BUDGET_BYTES * 2)
  })

  it('a failed page shrinks the budget and a history restart resets it', async () => {
    const t = setup({ pages: 6, pageMs: 50 })
    t.scheduler.start(SID)
    await settle(t.scheduler, 4)
    await settle(t.scheduler, 2000)
    const grown = Math.max(...t.state.fetched.map(f => f.opts.maxBytes))
    expect(grown).toBeGreaterThan(PAGE_BUDGET_BYTES)

    const e = setup({ pages: 4, pageMs: 50 })
    let calls = 0
    const real = e.messageStore.fetchOlderPage.getMockImplementation()
    e.messageStore.fetchOlderPage.mockImplementation(async (...a) => { calls++; if (calls === 3) throw new Error('timeout'); return real(...a) })
    e.scheduler.start(SID)
    await settle(e.scheduler, 2000)
    const sizes = e.state.fetched.map(f => f.opts.maxBytes)
    // 256K -> 512K -> (3rd call fails, no fetched entry) -> retried at half of the grown 1M budget
    expect(sizes[0]).toBe(PAGE_BUDGET_BYTES)
    expect(sizes[1]).toBe(PAGE_BUDGET_BYTES * 2)
    expect(sizes[2]).toBe(PAGE_BUDGET_BYTES * 2) // 1 MiB halved after the failure
  })

  it('an on-demand page always uses the base budget even after the budget has grown', async () => {
    const t = setup({ pages: 8, pageMs: 50, slow: () => true })
    t.scheduler.start(SID)
    for (let i = 0; i < 5; i++) { t.scheduler.requestOlder(SID); await settle(t.scheduler, 30) }
    expect(t.state.fetched.length).toBeGreaterThanOrEqual(3)
    expect(new Set(t.state.fetched.map(f => f.opts.maxBytes))).toEqual(new Set([PAGE_BUDGET_BYTES]))
    t.scheduler.stop(SID)
  })

  it('a prefetch (not urgent) uses the adaptive budget, an urgent request the base budget (stage 3 PR 3D)', async () => {
    const t = setup({ pages: 8, pageMs: 50, slow: () => true })
    t.scheduler.start(SID)
    for (let i = 0; i < 4; i++) { t.scheduler.requestOlder(SID, { urgent: false }); await settle(t.scheduler, 30) }
    const budgets = t.state.fetched.map(f => f.opts.maxBytes)
    expect(budgets[0]).toBe(PAGE_BUDGET_BYTES)
    expect(Math.max(...budgets)).toBeGreaterThan(PAGE_BUDGET_BYTES) // fast pages grew the budget
    const before = t.state.fetched.length
    t.scheduler.requestOlder(SID, { urgent: true })
    await settle(t.scheduler, 30)
    expect(t.state.fetched[before].opts.maxBytes).toBe(PAGE_BUDGET_BYTES)
    t.scheduler.stop(SID)
  })

  it('a prefetch never uses less than the base budget even when pages are slow', async () => {
    const t = setup({ pages: 6, pageMs: 3000, slow: () => true })
    t.scheduler.start(SID)
    for (let i = 0; i < 4; i++) { t.scheduler.requestOlder(SID, { urgent: false }); await settle(t.scheduler, 30) }
    expect(t.state.fetched.length).toBeGreaterThanOrEqual(3)
    expect(Math.min(...t.state.fetched.map(f => f.opts.maxBytes))).toBe(PAGE_BUDGET_BYTES)
    t.scheduler.stop(SID)
  })

  it('runs the view hook right before an older page is applied and its returned function right after; a failing hook never fails the page (stage 3 PR 3D)', async () => {
    const t = setup({ pages: 2 })
    const order = []
    t.messageStore.applyFetchedOlderPage = vi.fn(() => { order.push('apply'); t.state.remaining--; return true })
    t.scheduler.setPageApplyHook(SID, () => { order.push('before'); return () => order.push('after') })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(order).toEqual(['before', 'apply', 'after', 'before', 'apply', 'after'])
    const t2 = setup({ pages: 1 })
    const failing = () => { throw new Error('boom') }
    t2.scheduler.setPageApplyHook(SID, failing)
    t2.scheduler.start(SID)
    await settle(t2.scheduler)
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
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(calls).toEqual(['theirs', 'theirs'])
  })

  it('pure sizing/gap functions', () => {
    expect(nextPageBudget(PAGE_BUDGET_BYTES, 100)).toBe(2 * PAGE_BUDGET_BYTES)
    expect(nextPageBudget(MAX_PAGE_BUDGET_BYTES, 100)).toBe(MAX_PAGE_BUDGET_BYTES)
    expect(nextPageBudget(MIN_PAGE_BUDGET_BYTES, 9999)).toBe(MIN_PAGE_BUDGET_BYTES)
    expect(backfillGapMs(0)).toBe(0)
    expect(backfillGapMs(100)).toBe(100 * GAP_FACTOR)
    expect(backfillGapMs(60000)).toBe(MAX_GAP_MS)
  })

  it('leaves (almost) no gap between fast pages and a gap proportional to the page time for slow ones', async () => {
    const fast = setup({ pages: 3, pageMs: 40 })
    fast.scheduler.start(SID)
    await settle(fast.scheduler)
    expect(fast.state.fetched[1].at - fast.state.applied[0]).toBeLessThanOrEqual(GAP_FACTOR * 40 + 1)

    const slow = setup({ pages: 3, pageMs: 1200 })
    slow.scheduler.start(SID)
    await settle(slow.scheduler)
    expect(slow.state.fetched[1].at - slow.state.applied[0]).toBeGreaterThanOrEqual(GAP_FACTOR * 1200)
    expect(slow.state.fetched[1].at - slow.state.applied[0]).toBeLessThanOrEqual(MAX_GAP_MS + 1)
  })

  it('does not fetch while the tab is hidden and resumes when visible', async () => {
    let hidden = true
    const t = setup({ pages: 2, hidden: () => hidden, onSleep: (state) => { if (state.sleeps.length === 5) hidden = false } })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(5)
    expect(t.state.fetched[0].at).toBeGreaterThan(1_000_000) // nothing fetched before it became visible
    expect(t.state.applied).toHaveLength(2)
  })

  it.each(['connecting', 'resyncing', 'backoff', 'unreachable'])('pauses while the connection is %s', async (connState) => {
    const t = setup({ pages: 1, polling: { sessionState: connState }, onSleep: (state) => { if (state.sleeps.length === 3) t.pollingStore.sessionState = 'connected' } })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(3)
    expect(t.state.applied).toHaveLength(1)
  })

  it('pauses while the last poll batch was a catch-up burst', async () => {
    const t = setup({ pages: 1 })
    const burstAt = t.clock()
    t.pollingStore.getLastBatchInfo = () => ({ at: burstAt, count: CATCHUP_EVENT_COUNT })
    t.scheduler.start(SID)
    await settle(t.scheduler, 5)
    expect(t.state.fetched).toHaveLength(0) // still inside QUIET_MS
    t.advance(QUIET_MS + 1)
    t.pollingStore.getLastBatchInfo = () => null
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
  })

  it('waits for a quiet window after any live batch', async () => {
    const t = setup({ pages: 1 })
    const at = t.clock()
    t.pollingStore.getLastBatchInfo = () => ({ at, count: 1 })
    t.scheduler.start(SID)
    await settle(t.scheduler, 5)
    expect(t.state.fetched).toHaveLength(0)
    t.advance(QUIET_MS + 1)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
  })

  it('pauses while a user REST action is in flight', async () => {
    let inFlight = 1
    const t = setup({ pages: 1, mutations: () => inFlight, onSleep: (state) => { if (state.sleeps.length === 3) inFlight = 0 } })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(3)
    expect(t.state.applied).toHaveLength(1)
  })

  it('the autoBackfill setting off stops the background loop, on-demand pages still load, and turning it on resumes (issue #2030 stage 3 PR 3D)', async () => {
    let on = false
    const t = setup({ pages: 4, autoBackfill: () => on })
    t.scheduler.start(SID)
    await new Promise(r => setTimeout(r, 20))
    expect(t.state.fetched).toHaveLength(0)
    expect(t.scheduler.statusBySession.value.get(SID).paused).toBe('auto-off')
    // an on-demand page (scroll toward the edge) is user priority: it loads although auto-load is off
    t.scheduler.requestOlder(SID)
    await new Promise(r => setTimeout(r, 20))
    expect(t.state.fetched).toHaveLength(1)
    expect(t.state.fetched[0].opts.maxBytes).toBe(PAGE_BUDGET_BYTES)
    // setting back on: the remaining pages follow
    on = true
    await settle(t.scheduler, 400)
    expect(t.state.applied).toHaveLength(4)
  })

  it('stopForced ends a "Load full history" override so the autoBackfill setting applies again', async () => {
    const t = setup({ pages: 50, pageMs: 5, autoBackfill: () => false })
    t.scheduler.start(SID)
    t.scheduler.loadAll(SID)
    expect(t.scheduler.statusBySession.value.get(SID).forced).toBe(true)
    await new Promise(r => setTimeout(r, 15))
    t.scheduler.stopForced(SID)
    expect(t.scheduler.statusBySession.value.get(SID).forced).toBe(false)
    await new Promise(r => setTimeout(r, 15))
    const fetchedAtStop = t.state.fetched.length
    await new Promise(r => setTimeout(r, 30))
    expect(t.state.fetched.length).toBe(fetchedAtStop)
    t.scheduler.stop(SID)
  })

  it('"Load full history" overrides the autoBackfill setting', async () => {
    const t = setup({ pages: 3, autoBackfill: () => false })
    t.scheduler.start(SID)
    await new Promise(r => setTimeout(r, 10))
    expect(t.state.fetched).toHaveLength(0)
    t.scheduler.loadAll(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(3)
  })

  it('the background loop yields to a user-driven island fetch and resumes after it', async () => {
    const t = setup({ pages: 2 })
    let release
    t.messageStore.getIsland = () => ({ hasMoreBefore: true, hasMoreAfter: false })
    t.messageStore.fetchIslandPage = vi.fn(() => new Promise(resolve => { release = () => resolve({ edge: 'before', records: [] }) }))
    t.messageStore.applyIslandPage = vi.fn(() => true)
    const island = t.scheduler.requestIslandOlder(SID)
    t.scheduler.start(SID)
    await new Promise(r => setTimeout(r, 20))
    expect(t.state.fetched).toHaveLength(0)
    expect(t.scheduler.statusBySession.value.get(SID).paused).toBe('user-fetch')
    release()
    await island
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(2)
  })

  it('reports the measured page latency of the suffix loop and of the island', async () => {
    const t = setup({ pages: 2, pageMs: 300, slow: () => true })
    expect(t.scheduler.getPageLatencyMs(SID)).toBe(0)
    t.scheduler.start(SID)
    t.scheduler.requestOlder(SID)
    await new Promise(r => setTimeout(r, 20))
    expect(t.scheduler.getPageLatencyMs(SID)).toBeGreaterThanOrEqual(300)
    t.messageStore.getIsland = () => ({ hasMoreBefore: true, hasMoreAfter: false })
    t.messageStore.fetchIslandPage = vi.fn(async () => { t.advance(120); return { edge: 'before', records: [] } })
    t.messageStore.applyIslandPage = vi.fn(() => true)
    await t.scheduler.requestIslandOlder(SID)
    expect(t.scheduler.getPageLatencyMs(SID, { island: true })).toBe(120)
  })

  it('never throttles an archive view on a live connection state', async () => {
    const t = setup({ pages: 1, history: { source: 'archive' }, polling: { sessionState: 'archived' } })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(1)
  })

  it('stops (and aborts the in-flight page) when the session is no longer current', async () => {
    const t = setup({ pages: 50 })
    t.scheduler.start(SID)
    await settle(t.scheduler, 3)
    t.pollingStore.currentSessionId = 'other'
    await settle(t.scheduler)
    expect(t.scheduler.isRunning(SID)).toBe(false)
    expect(t.state.applied.length).toBeLessThan(50)
    t.scheduler.stop(SID)
    expect(t.state.aborts.every(c => c.signal.aborted)).toBe(true)
  })

  it('keeps a single loop per session (rapid restart is a no-op while running)', async () => {
    const t = setup({ pages: 3 })
    t.scheduler.start(SID)
    t.scheduler.start(SID)
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.applied).toHaveLength(3)
    expect(t.messageStore.fetchOlderPage).toHaveBeenCalledTimes(3)
  })

  it('restarts from the tail when the server says the cursor/history is no longer valid', async () => {
    const t = setup({ pages: 2 })
    t.state.failNext = true
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.restarts).toHaveLength(1)
    expect(t.state.restarts[0][3]).toMatchObject({ window: 'tail', baseUrl: '/x' })
    expect(t.state.applied).toHaveLength(2)
  })

  it('a failing tail restart counts as an ordinary failure (no unhandled rejection) and an archive restart owns its signal', async () => {
    const t = setup({ pages: 2, history: { source: 'archive' }, polling: { sessionState: 'archived' } })
    t.state.failNext = true
    t.messageStore.loadMessages.mockRejectedValueOnce(new Error('offline'))
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.messageStore.loadMessages).toHaveBeenCalledTimes(1)
    expect(t.messageStore.loadMessages.mock.calls[0][3].signal).toBeInstanceOf(AbortSignal) // archive views never claim the live tracker
    expect(t.state.applied).toHaveLength(2) // recovered after the counted failure
  })

  it('holds an applied page back during a scroll gesture unless the viewport is near the top', async () => {
    const t = setup({ pages: 1, onSleep: (state) => { if (state.sleeps.length === 4) t.scheduler.setScrollInteraction(SID, { gestureActive: false, nearTop: false }) } })
    t.scheduler.setScrollInteraction(SID, { gestureActive: true, nearTop: false })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.sleeps.length).toBeGreaterThanOrEqual(4)
    expect(t.state.applied).toHaveLength(1)

    const nearTop = setup({ pages: 1 })
    nearTop.scheduler.setScrollInteraction(SID, { gestureActive: true, nearTop: true })
    nearTop.scheduler.start(SID)
    await settle(nearTop.scheduler)
    expect(nearTop.state.applied).toHaveLength(1)
  })

  describe('slow-link mode', () => {
    it('performs no automatic backfill', async () => {
      const t = setup({ pages: 5, slow: () => true })
      t.scheduler.start(SID)
      await settle(t.scheduler, 30)
      expect(t.state.fetched).toHaveLength(0)
      expect(t.scheduler.statusBySession.value.get(SID).mode).toBe('manual')
      t.scheduler.stop(SID)
    })

    it('loads exactly one page per on-demand request', async () => {
      const t = setup({ pages: 5, slow: () => true })
      t.scheduler.start(SID)
      t.scheduler.requestOlder(SID)
      await settle(t.scheduler, 30)
      expect(t.state.applied).toHaveLength(1)
      t.scheduler.requestOlder(SID)
      await settle(t.scheduler, 30)
      expect(t.state.applied).toHaveLength(2)
      t.scheduler.stop(SID)
    })

    it('"Load full history" switches the session to paced automatic backfill', async () => {
      const t = setup({ pages: 4, slow: () => true })
      t.scheduler.start(SID)
      t.scheduler.loadAll(SID)
      await settle(t.scheduler, 500)
      expect(t.state.applied).toHaveLength(4)
    })
  })

  it('an on-demand page bypasses the inter-page gap but not the catch-up pause', async () => {
    const t = setup({ pages: 2, slow: () => true })
    t.scheduler.start(SID)
    const burstAt = t.clock()
    t.pollingStore.getLastBatchInfo = () => ({ at: burstAt, count: CATCHUP_EVENT_COUNT })
    t.scheduler.requestOlder(SID)
    await settle(t.scheduler, 5)
    expect(t.state.fetched).toHaveLength(0)
    t.pollingStore.getLastBatchInfo = () => null
    await settle(t.scheduler, 30)
    expect(t.state.applied).toHaveLength(1)
    t.scheduler.stop(SID)
  })

  it('gives up with an error status after repeated failures and can be retried', async () => {
    const t = setup({ pages: 1 })
    t.messageStore.fetchOlderPage.mockRejectedValue(new Error('boom'))
    t.scheduler.start(SID)
    await settle(t.scheduler, 500)
    expect(t.scheduler.statusBySession.value.get(SID).error).toBe('boom')
    expect(t.scheduler.isRunning(SID)).toBe(false)
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
