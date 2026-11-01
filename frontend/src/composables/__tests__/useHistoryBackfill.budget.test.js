import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { createFakeHistoryServer } from '@/stores/__tests__/helpers/historyServer'
import { createHistoryBackfill } from '../useHistoryBackfill'
import { TRIM_MIN_INTERVAL_MS, TRIM_RETRY_MS } from '@/utils/historyBudget'

// Issue #2030 stage 4 PR 4d: the scheduler against the REAL message store and the in-memory history
// server (cursor `b:<record index>`, total_bytes = record count, so budgets here are record counts):
// demand pages past the budget, the idle trim and its anti-thrash rules, the phone settle,
// and the background keep-bytes hook. Time is a virtual clock; timers are vitest fake timers.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({
  api: apiMock, getAuthToken: vi.fn().mockReturnValue(null), getMutationsInFlight: () => 0,
}))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const A = 'sess-a'
const B = 'sess-b'
const sig = () => new AbortController().signal

function buildRecords(turns, prefix = '') {
  const records = []
  for (let t = 0; t < turns; t++) {
    records.push({ type: 'user', message_id: `${prefix}u${t}`, timestamp: 1000 + t * 10, content: `turn ${t}`, metadata: {} })
    records.push({
      type: 'assistant', message_id: `${prefix}a${t}`, timestamp: 1001 + t * 10, content: `answer ${t}`,
      metadata: { turn_id: `turn-${t}`, has_tool_uses: true, tool_uses: [{ id: `${prefix}tool${t}`, name: 'Bash', input: {} }] },
    })
    records.push({
      type: 'tool_call', message_id: `${prefix}tc${t}`, tool_use_id: `${prefix}tool${t}`, name: 'Bash', status: 'completed',
      input: {}, result: `out ${t}`, turn_id: `turn-${t}`, created_at: 1002 + t * 10,
    })
  }
  return records
}

let clock
let hidden
let store
let polling
let servers
let notSticky

async function setup({ budget = 300, turns = 400, tail = 30, pageSize = 15, sessions = [A] } = {}) {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  store = useMessageStore()
  servers = {}
  for (const sid of sessions) {
    servers[sid] = createFakeHistoryServer(buildRecords(turns, sid === A ? '' : 'b-'), {
      tailStart: turns * 3 - tail, pageSize, historyId: `h-${sid}`,
    })
  }
  apiMock.get.mockImplementation((url) => {
    const sid = String(url).match(/sessions\/([^/]+)\/messages/)?.[1]
    return servers[sid].handle(url)
  })
  for (const sid of sessions) {
    await store.loadMessages(sid, null, 0, { window: 'tail', signal: sig(), baseUrl: `/api/sessions/${sid}/messages` })
  }
  polling = { currentSessionId: sessions[0], sessionState: 'connected', getLastBatchInfo: () => null }
  const scheduler = createHistoryBackfill({
    messageStore: () => store,
    pollingStore: () => polling,
    now: () => clock,
    sleep: async (ms) => { clock += ms; await new Promise(r => setTimeout(r, 0)) },
    idleSlot: async () => {},
    mutationsInFlight: () => 0,
    isHidden: () => hidden,
    isSlowLink: () => false,
    budgetBytes: () => budget,
    backgroundTrimDelayMs: 100,
    viewedSessionId: () => polling.currentSessionId,
    isStickyBottom: (sid) => notSticky.has(sid) === false,
  })
  return scheduler
}

const loaded = (sid = A) => store.historyProgress(sid).loadedBytes
const beforeFetches = (sid = A) => servers[sid].requests.filter(u => String(u).includes('before=')).length
async function settle(scheduler, sid = A, ticks = 4000) {
  for (let i = 0; i < ticks && scheduler.isRunning(sid); i++) await new Promise(r => setTimeout(r, 1))
}
// What a user scrolling up does: one demand page after another until `bytes` are loaded (or the history is complete).
async function loadTo(scheduler, sid, bytes) {
  for (let i = 0; i < 600 && loaded(sid) < bytes && !store.isHistoryComplete(sid); i++) {
    scheduler.requestOlder(sid, { urgent: false })
    await settle(scheduler, sid)
  }
}

beforeEach(() => {
  apiMock.get.mockReset()
  clock = 1_000_000
  hidden = false
  notSticky = new Set()
  localStorage.clear()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('demand pages are never refused by the budget', () => {
  it('keeps loading on demand far past the budget; an idle list then fetches nothing and keeps no timers', async () => {
    const scheduler = await setup({ budget: 300 })
    await loadTo(scheduler, A, 400)
    expect(loaded()).toBeGreaterThanOrEqual(400)
    expect(scheduler.isRunning(A)).toBe(false)
    const fetches = beforeFetches()
    scheduler.start(A) // e.g. the session view re-entering
    for (let i = 0; i < 20; i++) { clock += 60_000; await new Promise(r => setTimeout(r, 1)) }
    expect(beforeFetches()).toBe(fetches)
    vi.useFakeTimers()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('Retry after repeated failures fetches the page the user waits for even past the budget', async () => {
    const scheduler = await setup({ budget: 300 })
    await loadTo(scheduler, A, 300)
    const before = loaded()
    scheduler.retry(A)
    await settle(scheduler)
    expect(loaded()).toBeGreaterThan(before)
  })

  it('a history that fits the budget loads completely on demand', async () => {
    const scheduler = await setup({ budget: 10_000, turns: 100 })
    await loadTo(scheduler, A, 10_000)
    expect(store.isHistoryComplete(A)).toBe(true)
  })

  it('a long history is not loaded at all until it is asked for', async () => {
    const scheduler = await setup({ budget: 160, turns: 2000, pageSize: 20 })
    scheduler.start(A)
    await settle(scheduler)
    expect(beforeFetches()).toBe(0)
    expect(scheduler.isRunning(A)).toBe(false)
  })
})

describe('idle trim', () => {
  /** A user parked at the live end: any number of leading messages may go (rows far above the viewport). */
  const atBottom = (max = Infinity) => () => ({ maxCutMessages: max })

  async function overBudget(budget = 300) {
    const scheduler = await setup({ budget })
    scheduler.setTrimGuard(A, atBottom())
    await loadTo(scheduler, A, budget + 60)
    expect(loaded()).toBeGreaterThan(budget)
    scheduler.stop(A) // drops the real-timer trim checks armed while loading
    return scheduler
  }

  it('releases the old end to 0.75 x budget, makes the budget sticky and does not refetch', async () => {
    const scheduler = await overBudget(300)
    vi.useFakeTimers()
    scheduler.requestTrimCheck(A)
    await vi.advanceTimersByTimeAsync(2500)
    expect(loaded()).toBeLessThanOrEqual(225)
    expect(loaded()).toBeGreaterThan(225 - 20)
    expect(store.getHistoryState(A)).toMatchObject({ budgetReached: true })
    const fetches = beforeFetches()
    scheduler.start(A)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(beforeFetches()).toBe(fetches) // nothing refetches on its own
  })

  it('waits while a gesture is active, while hidden, and while the guard allows nothing', async () => {
    const scheduler = await overBudget(300)
    const start = loaded()
    vi.useFakeTimers()
    scheduler.setScrollInteraction(A, { gestureActive: true, nearTop: false })
    scheduler.requestTrimCheck(A)
    await vi.advanceTimersByTimeAsync(2500 + TRIM_RETRY_MS)
    expect(loaded()).toBe(start)
    scheduler.setScrollInteraction(A, { gestureActive: false, nearTop: false })
    hidden = true
    await vi.advanceTimersByTimeAsync(TRIM_RETRY_MS)
    expect(loaded()).toBe(start)
    hidden = false
    scheduler.setTrimGuard(A, () => ({ maxCutMessages: 0 }))
    await vi.advanceTimersByTimeAsync(TRIM_RETRY_MS * 2)
    expect(loaded()).toBe(start)
    scheduler.setTrimGuard(A, atBottom())
    await vi.advanceTimersByTimeAsync(TRIM_RETRY_MS * 2)
    expect(loaded()).toBeLessThan(start)
  })

  it('never removes more than the guard allows (rows near the viewport stay)', async () => {
    const scheduler = await overBudget(300)
    vi.useFakeTimers()
    scheduler.setTrimGuard(A, atBottom(40)) // 40 messages == 4 pages of 10
    scheduler.requestTrimCheck(A)
    await vi.advanceTimersByTimeAsync(2500)
    const trimmed = store.getHistoryState(A).trimmedBytes
    expect(trimmed).toBeGreaterThan(0)
    expect(trimmed).toBeLessThanOrEqual(4 * 15)
  })

  it('trims at most once per TRIM_MIN_INTERVAL_MS', async () => {
    const scheduler = await overBudget(300)
    vi.useFakeTimers()
    scheduler.setTrimGuard(A, atBottom(30))
    scheduler.requestTrimCheck(A)
    await vi.advanceTimersByTimeAsync(2500)
    const first = store.getHistoryState(A).trimmedBytes
    expect(first).toBeGreaterThan(0)
    // still over the target: the next one has to wait out the interval
    clock += 1000
    await vi.advanceTimersByTimeAsync(TRIM_RETRY_MS * 2)
    expect(store.getHistoryState(A).trimmedBytes).toBe(first)
    clock += TRIM_MIN_INTERVAL_MS
    await vi.advanceTimersByTimeAsync(TRIM_MIN_INTERVAL_MS + 2500)
    expect(store.getHistoryState(A).trimmedBytes).toBeGreaterThan(first)
  })

  it('a demand page cancels a pending trim, which is re-armed once the page landed', async () => {
    const scheduler = await overBudget(300)
    vi.useFakeTimers()
    scheduler.requestTrimCheck(A)
    const before = loaded()
    scheduler.requestOlder(A) // the user is heading to the old edge
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(2500)
    // the page landed first (virtual fetch), then the re-armed check trimmed
    expect(loaded()).toBeLessThanOrEqual(before)
    expect(store.getHistoryState(A).budgetReached).toBe(true)
  })

  it('after a trim, scrolling up refetches the released page and the status shows loading', async () => {
    const scheduler = await overBudget(300)
    vi.useFakeTimers()
    scheduler.requestTrimCheck(A)
    await vi.advanceTimersByTimeAsync(2500)
    expect(store.getHistoryState(A).trimmedBytes).toBeGreaterThan(0)
    const before = loaded()
    const fetches = beforeFetches()
    const realFetch = store.fetchOlderPage
    const loadingDuringFetch = []
    store.fetchOlderPage = (...args) => { loadingDuringFetch.push(scheduler.statusBySession.value.get(A).loading); return realFetch(...args) }
    scheduler.requestOlder(A, { urgent: false })
    await vi.advanceTimersByTimeAsync(1000)
    expect(loadingDuringFetch).toEqual([true])
    expect(beforeFetches()).toBe(fetches + 1)
    expect(loaded()).toBeGreaterThan(before)
    expect(scheduler.statusBySession.value.get(A).loading).toBe(false)
  })

  it('simulated viewport: prefetch near the old edge never fetches a page the trim just released', async () => {
    const scheduler = await overBudget(300)
    vi.useFakeTimers()
    // The viewport sits at message index `viewport`; rows >= 12 viewports (10 messages each) above it may go,
    // the scroll prefetch fires within 8 viewports of the old edge.
    const VH = 10
    const viewportFromOldEdge = () => store.messagesBySession.get(A).length - 5 // parked 5 messages from the live end
    const fetchesBefore = beforeFetches()
    scheduler.setTrimGuard(A, () => ({ maxCutMessages: Math.max(0, viewportFromOldEdge() - 12 * VH) }))
    scheduler.requestTrimCheck(A)
    await vi.advanceTimersByTimeAsync(2500)
    const releasedAt = clock
    for (let i = 0; i < 60; i++) {
      const distance = viewportFromOldEdge() // messages between the old edge and the viewport
      if (distance <= 8 * VH) scheduler.requestOlder(A, { urgent: false }) // what the prefetch would do
      await vi.advanceTimersByTimeAsync(1000)
    }
    expect(clock).toBe(releasedAt)
    expect(viewportFromOldEdge()).toBeGreaterThan(8 * VH) // the guard kept the edge out of prefetch range
    expect(beforeFetches()).toBe(fetchesBefore)
  })
})

describe('background sessions (LRU keep-bytes)', () => {
  it('a session that is no longer current keeps only its newest min(8 MiB, budget/4); live records survive', async () => {
    const scheduler = await setup({ budget: 400, sessions: [A, B] })
    await loadTo(scheduler, A, 400)
    expect(loaded(A)).toBeGreaterThanOrEqual(400)
    polling.currentSessionId = B
    store.applyRecord(A, { type: 'assistant', message_id: 'live-bg', content: 'x', timestamp: 9000, metadata: {} }, 'live')
    vi.useFakeTimers()
    scheduler.onSessionSwitch(A, B)
    await vi.advanceTimersByTimeAsync(500)
    // budget/4 == 100 in these units (8 MiB cap not reached); + one 15-record page and the live record's ~110 JSON "bytes"
    expect(loaded(A)).toBeLessThanOrEqual(100 + 15 + 120)
    expect(loaded(A)).toBeLessThan(200)
    expect(store.messagesBySession.get(A).at(-1).message_id).toBe('live-bg')
    expect(store.getHistoryState(A).budgetReached).toBe(false)
  })

  it('re-entry loads on demand from the kept tail', async () => {
    const scheduler = await setup({ budget: 400, sessions: [A, B] })
    await loadTo(scheduler, A, 400)
    polling.currentSessionId = B
    vi.useFakeTimers()
    scheduler.onSessionSwitch(A, B)
    await vi.advanceTimersByTimeAsync(500)
    vi.useRealTimers()
    expect(loaded(A)).toBeLessThanOrEqual(100 + 15)
    polling.currentSessionId = A
    scheduler.onSessionSwitch(B, A)
    await loadTo(scheduler, A, 400)
    expect(loaded(A)).toBeGreaterThanOrEqual(400)
    expect(loaded(A)).toBeLessThan(400 + 16)
  })

  it('coming back before the delay cancels the pending background trim', async () => {
    const scheduler = await setup({ budget: 400, sessions: [A, B] })
    await loadTo(scheduler, A, 400)
    const start = loaded(A)
    vi.useFakeTimers()
    polling.currentSessionId = B
    scheduler.onSessionSwitch(A, B)
    polling.currentSessionId = A
    scheduler.onSessionSwitch(B, A)
    await vi.advanceTimersByTimeAsync(500)
    expect(loaded(A)).toBe(start)
  })

  it('a list scrolled up (not following the live end) is left alone', async () => {
    const scheduler = await setup({ budget: 400, sessions: [A, B] })
    notSticky.add(A) // the list was scrolled up when the user left
    await loadTo(scheduler, A, 400)
    const start = loaded(A)
    polling.currentSessionId = B
    vi.useFakeTimers()
    scheduler.onSessionSwitch(A, B)
    await vi.advanceTimersByTimeAsync(500)
    expect(loaded(A)).toBe(start)
  })
})
