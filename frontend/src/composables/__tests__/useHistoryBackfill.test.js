import { describe, it, expect, vi } from 'vitest'
import {
  createHistoryBackfill, detectSlowLink,
  QUIET_MS, CATCHUP_EVENT_COUNT, SLOW_LINK_BPS, PAGE_BUDGET_BYTES, MIN_PAGE_BUDGET_BYTES, MAX_PAGE_BUDGET_BYTES,
  MAX_PAGE_RECORDS, MAX_GAP_MS, GAP_FACTOR, nextPageBudget, backfillGapMs, APPLY_OK_MS, APPLY_SLOW_MS,
} from '../useHistoryBackfill'

// Issue #2030 (stage 1b): backfill scheduler pacing matrix. Time is virtual: every sleep()
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

describe('requestUntil — turn-jump priority pages (issue #2030 stage 2)', () => {
  const settleJump = async (jump) => { await jump.promise.catch(() => {}) }

  it('resolves immediately when the target is already loaded', async () => {
    const t = setup({ pages: 3 })
    t.state.loadedAfter = 0
    const jump = t.scheduler.requestUntil(SID, 'm1', { ordinal: 2 })
    await expect(jump.promise).resolves.toBeUndefined()
    expect(t.state.fetched).toHaveLength(0)
  })

  it('fetches 512 KiB pages with no gap until the target is loaded, then resolves and clears the jump status', async () => {
    const t = setup({ pages: 10, pageMs: 600 })
    t.state.loadedAfter = 3
    const jump = t.scheduler.requestUntil(SID, 'm1', { ordinal: 4, fromOffset: 900, toOffset: 100 })
    expect(t.scheduler.statusBySession.value.get(SID).jump).toMatchObject({ messageId: 'm1', ordinal: 4 })
    await jump.promise
    expect(t.state.applied).toHaveLength(3)
    expect(t.state.fetched.map(f => f.opts.maxBytes)).toEqual([512 * 1024, 512 * 1024, 512 * 1024])
    // no inter-page gap even though pages took 600 ms each (the plain loop would sleep ~300 ms)
    expect(t.state.sleeps.filter(ms => ms > 0 && ms < 10000).every(ms => ms <= 250)).toBe(true)
    expect(t.scheduler.statusBySession.value.get(SID)?.jump ?? null).toBeNull()
  })

  it('works in slow-link (manual) mode, where nothing else loads', async () => {
    const t = setup({ pages: 6, slow: () => true })
    t.scheduler.start(SID)
    await new Promise(r => setTimeout(r, 20))
    expect(t.state.fetched).toHaveLength(0)
    t.state.loadedAfter = 2
    await t.scheduler.requestUntil(SID, 'm1').promise
    expect(t.state.applied).toHaveLength(2)
    expect(t.scheduler.getMode(SID)).toBe('manual')
  })

  it('cancel rejects with reason "cancelled", clears the status and leaves what was loaded', async () => {
    const t = setup({ pages: 50, slow: () => true })
    t.state.loadedAfter = 40
    let calls = 0
    const real = t.messageStore.fetchOlderPage.getMockImplementation()
    // the third page never completes until it is aborted (the user cancels while it is in flight)
    t.messageStore.fetchOlderPage.mockImplementation((sid, opts) => (++calls < 3 ? real(sid, opts) : new Promise((_res, rej) => {
      opts.signal.addEventListener('abort', () => rej(new DOMException('Aborted', 'AbortError')))
    })))
    const jump = t.scheduler.requestUntil(SID, 'm1', { ordinal: 9 })
    for (let i = 0; i < 100 && calls < 3; i++) await new Promise(r => setTimeout(r, 1))
    expect(calls).toBe(3)
    jump.cancel()
    await expect(jump.promise).rejects.toMatchObject({ reason: 'cancelled' })
    expect(t.state.applied).toHaveLength(2)
    expect(t.scheduler.statusBySession.value.get(SID)?.jump ?? null).toBeNull()
    t.scheduler.stop(SID)
  })

  it('cancelJump (the banner button) cancels the pending jump', async () => {
    const t = setup({ pages: 50, slow: () => true })
    t.state.loadedAfter = 40
    const jump = t.scheduler.requestUntil(SID, 'm1')
    t.scheduler.cancelJump(SID)
    await expect(jump.promise).rejects.toMatchObject({ reason: 'cancelled' })
  })

  it('rejects with "aborted" when the session stops being current mid-jump', async () => {
    const t = setup({ pages: 50, pageMs: 50 })
    t.state.loadedAfter = 40
    const jump = t.scheduler.requestUntil(SID, 'm1')
    t.pollingStore.currentSessionId = 'other'
    await expect(jump.promise).rejects.toMatchObject({ reason: 'aborted' })
  })

  it('rejects with "not-found" when the history completes without the target', async () => {
    const t = setup({ pages: 2 })
    const jump = t.scheduler.requestUntil(SID, 'm1')
    await expect(jump.promise).rejects.toMatchObject({ reason: 'not-found' })
  })

  it('a second jump supersedes the first', async () => {
    const t = setup({ pages: 50, slow: () => true })
    t.state.loadedAfter = 3
    const first = t.scheduler.requestUntil(SID, 'm1')
    const second = t.scheduler.requestUntil(SID, 'm2')
    await expect(first.promise).rejects.toMatchObject({ reason: 'superseded' })
    await second.promise
  })

  it('still pauses while the live stream is catching up (priority, not a bypass of the pause rules)', async () => {
    let t
    t = setup({ pages: 5, polling: { getLastBatchInfo: () => ({ at: t.clock(), count: CATCHUP_EVENT_COUNT }) } })
    t.state.loadedAfter = 1
    const jump = t.scheduler.requestUntil(SID, 'm1')
    await new Promise(r => setTimeout(r, 5))
    expect(t.state.fetched).toHaveLength(0)
    t.pollingStore.getLastBatchInfo = () => null
    await jump.promise
    expect(t.state.fetched).toHaveLength(1)
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
