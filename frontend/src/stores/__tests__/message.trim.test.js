import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { createFakeHistoryServer } from './helpers/historyServer'
import { normalizeForComparison } from './helpers/fixtureEquivalence'
import { buildDisplayableItems } from '@/utils/displayableItems'

// Issue #2030 stage 4 PR 4d: the segment ledger, `trimOldEnd` (oldest-end release by exact cursor),
// tool ownership/pins, round-trip equivalence and island far-edge eviction. The fake history server's
// "bytes" are records (cursor `b:<index>`, total_bytes = record count), so budgets here are record counts.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-trim'
const sig = () => new AbortController().signal

/** Per turn: user, assistant with one tool_use, and the tool's tool_call record (3 records). */
function buildRecords(turns, { statusOf = () => 'completed' } = {}) {
  const records = []
  for (let t = 0; t < turns; t++) {
    records.push({ type: 'user', message_id: `u${t}`, timestamp: 1000 + t * 10, content: `turn ${t}`, metadata: {} })
    records.push({
      type: 'assistant', message_id: `a${t}`, timestamp: 1001 + t * 10, content: `answer ${t}`,
      metadata: { turn_id: `turn-${t}`, has_tool_uses: true, tool_uses: [{ id: `tool${t}`, name: 'Bash', input: { command: `echo ${t}` } }] },
    })
    records.push({
      type: 'tool_call', message_id: `tc${t}`, tool_use_id: `tool${t}`, name: 'Bash', status: statusOf(t),
      input: { command: `echo ${t}` }, result: statusOf(t) === 'completed' ? `out ${t}` : undefined,
      turn_id: `turn-${t}`, created_at: 1002 + t * 10,
    })
  }
  return records
}

async function setup(records, { tail = 30, pageSize = 15, ...options } = {}) {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const store = useMessageStore()
  const server = createFakeHistoryServer(records, { tailStart: records.length - tail, pageSize, ...options })
  apiMock.get.mockImplementation((url) => server.handle(url))
  await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
  return { store, server }
}

async function backfillAll(store, { until = null } = {}) {
  let guard = 0
  while (!store.isHistoryComplete(SID)) {
    if (guard++ > 5000) throw new Error('backfill did not terminate')
    const page = await store.fetchOlderPage(SID, { signal: sig() })
    expect(store.applyFetchedOlderPage(SID, page)).toBe(true)
    if (until && until(store)) return
  }
}

function snapshot(store) {
  const messages = store.messagesBySession.get(SID) || []
  const toolCalls = store.toolCallsBySession.get(SID) || []
  const items = buildDisplayableItems({
    sessionId: SID,
    messages,
    toolCalls,
    legEntries: store.allTaskLegEntriesForSession(SID),
    getTaskIdForLaunchToolUse: store.getTaskIdForLaunchToolUse,
    isHookMessageAttached: (id) => store.isHookMessageAttached(SID, id),
    openBefore: !store.isHistoryComplete(SID),
  })
  return normalizeForComparison({
    messages,
    toolCalls,
    messageIndex: [...store.messageIndexBySession.get(SID)],
    toolIndex: [...store.toolIndexBySession.get(SID)],
    items,
  })
}

const ids = (list) => list.map(m => m.message_id)

beforeEach(() => {
  apiMock.get.mockReset()
  localStorage.clear()
})
afterEach(() => localStorage.clear())

describe('ledger + trimOldEnd', () => {
  it('records one exact segment per older page and cuts whole pages off the old end', async () => {
    const records = buildRecords(60) // 180 records, tail 30 -> pages at 150,135,...,0
    const { store } = await setup(records)
    await backfillAll(store)
    expect(store.getTrimInfo(SID)).toMatchObject({ segments: 10, loadedBytes: 180 })

    const result = store.trimOldEnd(SID, { targetBytes: 100 })
    expect(result.trimmed).toBe(true)
    // oldest boundaries first until total - start <= 100: the first boundary >= 80 is 90
    expect(store.getHistoryState(SID)).toMatchObject({ startCursor: 'b:90', hasMoreBefore: true, budgetReached: true, trimmedBytes: 90 })
    const messages = store.messagesBySession.get(SID)
    expect(ids(messages)[0]).toBe('u30') // record 90 == turn 30's user row
    expect(messages).toHaveLength(60 * 2 - 60)
    expect(store.toolCallsBySession.get(SID)).toHaveLength(30)
    // indices rebuilt in step
    for (const [id, idx] of store.messageIndexBySession.get(SID)) expect(messages[idx].message_id).toBe(id)
    for (const [id, idx] of store.toolIndexBySession.get(SID)) expect(store.toolCallsBySession.get(SID)[idx].id).toBe(id)
    expect(store.isHistoryComplete(SID)).toBe(false)
  })

  it('notifies dependents (Map replacement) and leaves the generation alone', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    const generation = store.getHistoryState(SID).generation
    const before = store.messagesBySession
    store.trimOldEnd(SID, { targetBytes: 100 })
    expect(store.messagesBySession).not.toBe(before)
    expect(store.getHistoryState(SID).generation).toBe(generation)
  })

  it('the next before= fetch returns exactly the released page (cursor correctness)', async () => {
    const { store, server } = await setup(buildRecords(60))
    await backfillAll(store)
    store.trimOldEnd(SID, { targetBytes: 100 })
    const page = await store.fetchOlderPage(SID, { signal: sig() })
    expect(server.requests.at(-1)).toContain('before=b%3A90')
    expect(page.records.map(r => r.message_id)).toEqual(
      buildRecords(60).slice(75, 90).map(r => r.message_id)
    )
    expect(store.applyFetchedOlderPage(SID, page)).toBe(true)
    expect(ids(store.messagesBySession.get(SID))[0]).toBe('u25')
  })

  it('does nothing under the target, without a ledger, or with an opaque cursor', async () => {
    const { store } = await setup(buildRecords(60))
    expect(store.trimOldEnd(SID, { targetBytes: 0 })).toMatchObject({ trimmed: false, reason: 'no-ledger' }) // tail only
    await backfillAll(store)
    expect(store.trimOldEnd(SID, { targetBytes: 1000 })).toMatchObject({ trimmed: false, reason: 'under-target' })
    expect(store.trimOldEnd('nope', { targetBytes: 0 })).toMatchObject({ trimmed: false })
  })

  it('maxCutMessages bounds the cut at page granularity', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    // each 15-record page holds 10 messages (u + a per turn, 5 turns); allow 2 pages
    const result = store.trimOldEnd(SID, { targetBytes: 0, maxCutMessages: 25 })
    expect(result.trimmed).toBe(true)
    expect(store.getHistoryState(SID).startCursor).toBe('b:30')
    expect(store.trimOldEnd(SID, { targetBytes: 0, maxCutMessages: 0 })).toMatchObject({ trimmed: false, reason: 'guard' })
  })

  it('a background trim does not make the budget sticky', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    store.trimOldEnd(SID, { targetBytes: 40, kind: 'background' })
    expect(store.getHistoryState(SID).budgetReached).toBe(false)
  })

  it('a changed generation (reset / replaced window) drops the ledger', async () => {
    const records = buildRecords(60)
    const { store } = await setup(records)
    await backfillAll(store)
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() }) // replaces the suffix: generation bump
    expect(store.getTrimInfo(SID)).toBeNull()
    expect(store.trimOldEnd(SID, { targetBytes: 0 })).toMatchObject({ trimmed: false })
  })

  it('live records appended after a trim land after the kept tail and survive further trims', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    store.applyRecord(SID, { type: 'assistant', message_id: 'live1', content: 'x', timestamp: 9000, metadata: {} }, 'live')
    store.trimOldEnd(SID, { targetBytes: 100 })
    expect(store.messagesBySession.get(SID).at(-1).message_id).toBe('live1')
    store.applyRecord(SID, { type: 'assistant', message_id: 'live2', content: 'y', timestamp: 9001, metadata: {} }, 'live')
    expect(store.messageIndexBySession.get(SID).get('live2')).toBe(store.messagesBySession.get(SID).length - 1)
  })
})

describe('budget accounting and state across resets', () => {
  it('live records count towards the loaded bytes and the trim math', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    const before = store.historyProgress(SID).loadedBytes
    expect(before).toBe(180)
    store.applyRecord(SID, { type: 'assistant', message_id: 'live-big', content: 'x'.repeat(500), timestamp: 9000, metadata: {} }, 'live')
    const after = store.historyProgress(SID).loadedBytes
    expect(after).toBeGreaterThan(before + 500)
    // the trim target is measured against the live-grown size
    // 180 file bytes + the live record: a 600-byte target needs everything older than the tail window gone
    store.trimOldEnd(SID, { targetBytes: 600 })
    expect(store.getHistoryState(SID).startCursor).toBe('b:150') // the tail window itself is never cut
    expect(store.historyProgress(SID).loadedBytes).toBe(after - 150)
  })

  it('a replaced suffix forgets the trimmed / budget state of the old one and the live byte count', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    store.applyRecord(SID, { type: 'assistant', message_id: 'live-1', content: 'x'.repeat(300), timestamp: 9000, metadata: {} }, 'live')
    store.trimOldEnd(SID, { targetBytes: 100 })
    expect(store.getHistoryState(SID)).toMatchObject({ budgetReached: true })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    expect(store.getHistoryState(SID)).toMatchObject({ budgetReached: false, trimmedBytes: 0 })
    expect(store.historyProgress(SID).loadedBytes).toBe(30) // the 30-record tail, no stale live bytes
  })

  it('a merged island keeps its page structure in the ledger, so a trim can release it page by page', async () => {
    const { store } = await setup(buildRecords(60), { tail: 30, pageSize: 15, aroundBefore: 6, aroundAfter: 3, afterPageSize: 15 })
    await backfillAll(store, { until: (s) => s.getHistoryState(SID).startCursor === 'b:120' })
    store.trimOldEnd(SID, { targetBytes: 40 })
    const segmentsBefore = store.getTrimInfo(SID)?.segments ?? 0
    await store.loadAround(SID, { messageId: 'u3' })
    for (let i = 0; store.getIsland(SID) && i < 100; i++) {
      const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
      store.applyIslandPage(SID, page)
    }
    expect(store.getIsland(SID)).toBeNull()
    expect(store.getTrimInfo(SID).segments).toBeGreaterThan(segmentsBefore + 2) // several pages, not one blob
    const startBefore = store.getHistoryState(SID).startCursor
    store.trimOldEnd(SID, { targetBytes: store.historyProgress(SID).loadedBytes - 20, maxCutMessages: 30 })
    const startAfter = store.getHistoryState(SID).startCursor
    expect(Number(startAfter.slice(2))).toBeGreaterThan(Number(startBefore.slice(2)))
    expect(Number(startAfter.slice(2)) - Number(startBefore.slice(2))).toBeLessThan(60) // a few pages, not the whole island
  })
})

describe('subagent legs', () => {
  it('keeps the tools running under a known leg although their messages were released', async () => {
    const records = buildRecords(60)
    records.splice(2, 0, { // a child tool of the leg launched by tool0, inside the released range
      type: 'tool_call', message_id: 'tc-child', tool_use_id: 'child-tool', name: 'Read', status: 'completed',
      input: {}, result: 'x', parent_tool_use_id: 'tool0', turn_id: 'turn-0', created_at: 1000.5,
    })
    const { store } = await setup(records)
    await backfillAll(store)
    store.applyTaskLifecycleFrame(SID, 'task_started', { task_id: 'task-1', tool_use_id: 'tool0', description: 'd' }, 1000)
    store.trimOldEnd(SID, { targetBytes: 30 })
    const ids = store.toolCallsBySession.get(SID).map(t => t.id)
    expect(ids).toContain('tool0')
    expect(ids).toContain('child-tool')
    expect(ids).not.toContain('tool1')
  })
})

describe('round trip: load full -> trim -> backfill back == original', () => {
  it.each([[30, 15, 100], [30, 7, 60], [12, 20, 40], [90, 5, 150]])(
    'tail=%i page=%i target=%i', async (tail, pageSize, target) => {
      const records = buildRecords(60)
      const { store } = await setup(records, { tail, pageSize })
      await backfillAll(store)
      const original = snapshot(store)
      const trimmed = store.trimOldEnd(SID, { targetBytes: target })
      expect(trimmed.trimmed).toBe(true)
      expect(snapshot(store)).not.toEqual(original)
      await backfillAll(store)
      expect(snapshot(store)).toEqual(original)
    })

  it('repeated trims and re-fills stay equal and never duplicate', async () => {
    const records = buildRecords(80)
    const { store } = await setup(records, { tail: 30, pageSize: 10 })
    await backfillAll(store)
    const original = snapshot(store)
    for (const target of [150, 90, 200, 60]) {
      store.trimOldEnd(SID, { targetBytes: target })
      await backfillAll(store, { until: (s) => (s.getHistoryState(SID).startCursor === 'b:0' ? true : Math.random() < 0.3) })
    }
    await backfillAll(store)
    expect(snapshot(store)).toEqual(original)
  })
})

describe('tool ownership', () => {
  /** u0..: tool of turn 0 gets `status`; everything else completes. Returns the store trimmed to the tail turns. */
  async function trimmedWith(statusOfTurn0, prepare) {
    const records = buildRecords(60, { statusOf: (t) => (t === 0 ? statusOfTurn0 : 'completed') })
    const { store } = await setup(records)
    await backfillAll(store)
    prepare?.(store)
    store.trimOldEnd(SID, { targetBytes: 30 })
    return store
  }
  const toolIds = (store) => store.toolCallsBySession.get(SID).map(t => t.id)

  it('releases completed tools of the removed messages', async () => {
    const store = await trimmedWith('completed')
    expect(toolIds(store)).not.toContain('tool0')
    expect(toolIds(store)).toContain('tool59')
  })

  it.each([['running'], ['awaiting_permission'], ['pending']])('keeps a %s tool of a removed message (pinned)', async (status) => {
    const store = await trimmedWith(status)
    expect(toolIds(store)).toContain('tool0')
    expect(store.messagesBySession.get(SID).some(m => m.message_id === 'a0')).toBe(false)
  })

  it('keeps the launch tool of a known subagent leg', async () => {
    const store = await trimmedWith('completed', (s) => {
      s.applyTaskLifecycleFrame(SID, 'task_started', { task_id: 'task-1', tool_use_id: 'tool0', description: 'd' }, 1000)
    })
    expect(toolIds(store)).toContain('tool0')
    expect(store.getTaskIdForLaunchToolUse('tool0')).toBe('task-1')
  })

  it('keeps a tool that a retained message also references', async () => {
    const records = buildRecords(60)
    records[3 * 59 + 1].metadata.tool_uses.push({ id: 'tool0', name: 'Bash', input: {} }) // last assistant row re-uses tool0
    const { store } = await setup(records)
    await backfillAll(store)
    store.trimOldEnd(SID, { targetBytes: 30 })
    expect(toolIds(store)).toContain('tool0')
  })

  it('keeps a tool whose records are not all in the released region (a late update after the cut)', async () => {
    const records = buildRecords(60)
    // tool0's start sits in turn 0 (released), its completion arrives 20 records before the end (retained)
    const startRecord = { ...records[2], status: 'running', result: undefined }
    const doneRecord = { ...records[2], message_id: 'tc0-done', status: 'completed', result: 'late', created_at: 2000 }
    records[2] = startRecord
    records.splice(records.length - 20, 0, doneRecord)
    const { store } = await setup(records)
    await backfillAll(store)
    expect(store.findLoadedToolCall(SID, 'tool0')).toMatchObject({ status: 'completed' })
    store.trimOldEnd(SID, { targetBytes: 30 })
    expect(toolIds(store)).toContain('tool0')
    expect(store.findLoadedToolCall(SID, 'tool0')).toMatchObject({ status: 'completed' })
  })

  it('a live update clears the release eligibility of an old tool', async () => {
    const { store } = await setup(buildRecords(60))
    await backfillAll(store)
    store.applyRecord(SID, { type: 'tool_call', message_id: 'tc-live', tool_use_id: 'tool1', name: 'Bash', status: 'completed', input: { command: 'echo 1' }, result: 'again', created_at: 9000 }, 'live')
    store.trimOldEnd(SID, { targetBytes: 30 })
    expect(toolIds(store)).toContain('tool1')
    expect(toolIds(store)).not.toContain('tool2')
  })

  it('a pinned tool whose message was removed is still found by findLoadedToolCall', async () => {
    const store = await trimmedWith('running')
    expect(store.findLoadedToolCall(SID, 'tool0')).toMatchObject({ id: 'tool0', status: 'executing' })
  })
})

describe('island far-edge eviction (replaces the interim cap)', () => {
  // budget 0.0003 MiB ~ 314 "bytes"; one island page = 15 records
  async function island(records, options) {
    localStorage.setItem('historyBudgetMiB', String(0.0003))
    const ctx = await setup(records, { tail: 12, pageSize: 15, afterPageSize: 15, aroundBefore: 20, aroundAfter: 20, ...options })
    return ctx
  }

  it('growing upward past the budget releases the lower (far) end by pages and flags hasMoreAfter', async () => {
    const records = buildRecords(400) // 1200 records
    const { store } = await island(records)
    await store.loadAround(SID, { cursor: 'b:700' })
    expect(store.getIsland(SID).hasMoreAfter).toBe(true) // base window, more behind it
    let evicted = false
    for (let i = 0; i < 60 && !evicted; i++) {
      const before = store.getIsland(SID)
      const page = await store.fetchIslandPage(SID, 'before', { signal: sig() })
      expect(store.applyIslandPage(SID, page)).toBe(true)
      const now = store.getIsland(SID)
      evicted = now.endCursor !== before.endCursor
      expect(now.bytes).toBeLessThanOrEqual(314)
    }
    expect(evicted).toBe(true)
    const after = store.getIsland(SID)
    expect(after.bytes).toBeLessThanOrEqual(235)
    expect(after.hasMoreAfter).toBe(true)
    expect(after.hasMoreBefore).toBe(true)
    // the content matches the claimed window exactly
    const startIdx = Number(/b:(\d+)/.exec(after.startCursor)[1])
    const endIdx = Number(/b:(\d+)/.exec(after.endCursor)[1])
    const expected = records.slice(startIdx, endIdx).filter(r => r.type !== 'tool_call').map(r => r.message_id)
    expect(ids(after.messages)).toEqual(expected)
  })

  it('growing downward past the budget releases the upper (far) end and flags hasMoreBefore', async () => {
    const records = buildRecords(400)
    const { store } = await island(records)
    await store.loadAround(SID, { cursor: 'b:200' })
    for (let i = 0; i < 40 && store.getIsland(SID); i++) {
      const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
      if (!page) break
      store.applyIslandPage(SID, page)
    }
    const after = store.getIsland(SID)
    expect(after.bytes).toBeLessThanOrEqual(314)
    expect(after.hasMoreBefore).toBe(true)
    const startIdx = Number(/b:(\d+)/.exec(after.startCursor)[1])
    expect(startIdx).toBeGreaterThan(200 - 41)
    for (const m of after.messages) expect(m.message_id).toBeTruthy()
    expect(new Set(ids(after.messages)).size).toBe(after.messages.length)
  })

  it('island tools of released messages go too, pinned ones stay', async () => {
    const records = buildRecords(400, { statusOf: (t) => (t === 42 ? 'running' : 'completed') })
    const { store } = await island(records, { aroundBefore: 30, aroundAfter: 30 })
    await store.loadAround(SID, { cursor: 'b:150' })
    for (let i = 0; i < 60 && store.getIsland(SID); i++) {
      const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
      if (!page) break
      store.applyIslandPage(SID, page)
    }
    const after = store.getIsland(SID)
    expect(after.hasMoreBefore).toBe(true)
    const toolIds = after.toolCalls.map(t => t.id)
    expect(toolIds).toContain('tool42') // running: pinned although its message was released
    expect(toolIds).not.toContain('tool41')
    expect(after.messages.some(m => m.message_id === 'a42')).toBe(false)
  })
})

describe('jump / search / deep link into the released range', () => {
  it('an evicted message is not loaded; loadAround opens an island with a gap, which merges back and stays trim-able', async () => {
    const records = buildRecords(60)
    const { store } = await setup(records, { tail: 30, pageSize: 15, aroundBefore: 6, aroundAfter: 3, afterPageSize: 15 })
    await backfillAll(store)
    const original = snapshot(store)
    store.trimOldEnd(SID, { targetBytes: 60 })
    const start = store.getHistoryState(SID).startCursor
    expect(store.isMessageLoaded(SID, 'u3')).toBe(false)

    const result = await store.loadAround(SID, { messageId: 'u3' })
    expect(result.status).toBe('island')
    expect(store.isMessageLoaded(SID, 'u3')).toBe(true)
    expect(store.getIsland(SID)).toMatchObject({ hasMoreAfter: true })
    // the suffix is untouched by the jump
    expect(store.getHistoryState(SID).startCursor).toBe(start)

    let guard = 0
    while (store.getIsland(SID) && guard++ < 100) {
      const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
      expect(store.applyIslandPage(SID, page)).toBe(true)
    }
    expect(store.getIsland(SID)).toBeNull() // merged into the suffix
    expect(store.isMessageLoaded(SID, 'u3')).toBe(true)
    expect(new Set(ids(store.messagesBySession.get(SID))).size).toBe(store.messagesBySession.get(SID).length)

    // the merged window is a ledger segment too: trim still lands on an exact cursor, refill restores everything
    const again = store.trimOldEnd(SID, { targetBytes: 20 })
    expect(again.trimmed).toBe(true)
    await backfillAll(store)
    expect(snapshot(store)).toEqual(original)
  })

  it('a search hit cursor in the released range opens through loadAround(cursor) as well', async () => {
    const { store } = await setup(buildRecords(60), { tail: 30, pageSize: 15 })
    await backfillAll(store)
    store.trimOldEnd(SID, { targetBytes: 60 })
    const result = await store.loadAround(SID, { cursor: 'b:12' })
    expect(result.status).toBe('island')
    expect(store.getIsland(SID).messages.length).toBeGreaterThan(0)
  })

  it('a trim with an island present keeps the island and its tools', async () => {
    const { store } = await setup(buildRecords(60), { tail: 30, pageSize: 15, aroundBefore: 6, aroundAfter: 3 })
    await backfillAll(store)
    await store.trimOldEnd(SID, { targetBytes: 120 })
    await store.loadAround(SID, { messageId: 'u3' })
    const island = store.getIsland(SID)
    store.trimOldEnd(SID, { targetBytes: 30, kind: 'background' })
    expect(store.getIsland(SID).messages).toHaveLength(island.messages.length)
    expect(store.getIsland(SID).toolCalls).toHaveLength(island.toolCalls.length)
  })
})
