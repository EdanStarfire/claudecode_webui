import { describe, it, expect, vi } from 'vitest'
import {
  createHistoryBackfill, detectSlowLink,
  MIN_GAP_MS, QUIET_MS, CATCHUP_EVENT_COUNT, SLOW_LINK_BPS, PAGE_BUDGET_BYTES,
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

  it('fetches with the fixed page budget', async () => {
    const t = setup({ pages: 2 })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    expect(t.state.fetched[0].opts.maxBytes).toBe(PAGE_BUDGET_BYTES)
  })

  it('waits max(500 ms, 2x the last page time) between pages', async () => {
    const t = setup({ pages: 3, pageMs: 100 })
    t.scheduler.start(SID)
    await settle(t.scheduler)
    // 2 x 100 ms < 500 ms -> the 500 ms floor applies between page 1's end and page 2's start.
    const gap1 = t.state.fetched[1].at - t.state.applied[0]
    expect(gap1).toBeGreaterThanOrEqual(MIN_GAP_MS)

    const slow = setup({ pages: 3, pageMs: 1200 })
    slow.scheduler.start(SID)
    await settle(slow.scheduler)
    const gap = slow.state.fetched[1].at - slow.state.applied[0]
    expect(gap).toBeGreaterThanOrEqual(2 * 1200)
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
