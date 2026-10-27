import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { listRawFixtureNames, loadRawFixture, normalizeForComparison } from './helpers/fixtureEquivalence'
import { scaleRestHistoryMessages } from './helpers/scaleFixture'
import { createFakeHistoryServer } from './helpers/historyServer'
import { buildDisplayableItems } from '@/utils/displayableItems'

// Issue #2030 (stage 1b, AC8 extension): a session loaded tail-first and then backfilled page by
// page must end up in EXACTLY the state a full load produces — messages, tool calls (including
// each tool's `input`, which an older record would clobber with `{}` if applied naively), hook
// correlation, and the rendered row list. Checked over many tail sizes x page sizes, at every
// split point of the small fixtures, with live events overlapping the tail, and across resync.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SESSION_ID = 'sess-equiv'

async function freshStore() {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  return useMessageStore()
}

function serve(server) {
  apiMock.get.mockImplementation((url) => server.handle(url))
}

/** Loads the tail window, then backfills every older page until `has_more_before` is false. */
async function loadTailThenBackfill(store, records, serverOptions, { beforePage } = {}) {
  const server = createFakeHistoryServer(records, serverOptions)
  serve(server)
  await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
  let guard = 0
  while (!store.isHistoryComplete(SESSION_ID)) {
    if (guard++ > 10000) throw new Error('backfill did not terminate')
    beforePage?.(store)
    const page = await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal })
    expect(store.applyFetchedOlderPage(SESSION_ID, page)).toBe(true)
  }
  return server
}

async function loadFull(records) {
  const store = await freshStore()
  const server = createFakeHistoryServer(records, { tailStart: 0 })
  serve(server)
  await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
  expect(store.isHistoryComplete(SESSION_ID)).toBe(true)
  return store
}

function snapshot(store) {
  const messages = store.messagesBySession.get(SESSION_ID) || []
  const toolCalls = store.toolCallsBySession.get(SESSION_ID) || []
  const items = buildDisplayableItems({
    sessionId: SESSION_ID,
    messages,
    toolCalls,
    legEntries: store.allTaskLegEntriesForSession(SESSION_ID),
    getTaskIdForLaunchToolUse: store.getTaskIdForLaunchToolUse,
    isHookMessageAttached: (id) => store.isHookMessageAttached(SESSION_ID, id),
    historyComplete: store.isHistoryComplete(SESSION_ID),
  })
  return normalizeForComparison({
    messages,
    toolCalls,
    messageIndex: [...(store.messageIndexBySession.get(SESSION_ID) || new Map())],
    toolIndex: [...(store.toolIndexBySession.get(SESSION_ID) || new Map())],
    lastStopReason: store.lastStopReasonBySession.get(SESSION_ID) ?? null,
    deferredToolUse: store.deferredToolUseBySession.get(SESSION_ID) ?? null,
    launchTimestamp: store.launchTimestampBySession.get(SESSION_ID) ?? null,
    hooksByTool: toolCalls.map(t => store.hooksForToolCall(SESSION_ID, t.id)),
    hooksByMessage: messages.map(m => store.hooksForMessageId(SESSION_ID, m.message_id)),
    items,
  })
}

beforeEach(() => {
  apiMock.get.mockReset()
})

const fixtureNames = listRawFixtureNames()
// The largest committed fixture — the scenario tests below need real room on both sides of a window.
const largestFixture = [...fixtureNames].sort(
  (a, b) => loadRawFixture(b).restHistory.messages.length - loadRawFixture(a).restHistory.messages.length
)[0]

describe('tail window + backfill == full load (AC8, issue #2030)', () => {
  describe.each(fixtureNames)('fixture %s', (name) => {
    const records = loadRawFixture(name).restHistory.messages

    it('matches a full load at every split point (single older page)', async () => {
      const expected = snapshot(await loadFull(records))
      const step = Math.max(1, Math.floor(records.length / 120))
      for (let tailStart = 1; tailStart < records.length; tailStart += step) {
        const store = await freshStore()
        await loadTailThenBackfill(store, records, { tailStart, pageSize: records.length })
        expect(snapshot(store), `tailStart=${tailStart}`).toEqual(expected)
      }
    })

    it.each([
      [30, 7], [30, 50], [100, 13], [records.length >> 1, 1], [5, 3],
    ])('matches a full load for tail=%i records, page=%i records', async (tailSize, pageSize) => {
      const expected = snapshot(await loadFull(records))
      const store = await freshStore()
      await loadTailThenBackfill(store, records, {
        tailStart: Math.max(1, records.length - tailSize), pageSize,
      })
      expect(snapshot(store)).toEqual(expected)
    })
  })

  it('matches a full load on the scaled 20k-record fixture', async () => {
    const base = loadRawFixture(largestFixture).restHistory.messages
    const records = scaleRestHistoryMessages(base, 20000)
    const expected = snapshot(await loadFull(records))
    const store = await freshStore()
    await loadTailThenBackfill(store, records, { tailStart: records.length - 150, pageSize: 1500 })
    expect(snapshot(store)).toEqual(expected)
  }, 120000)
})

describe('live events, resync and history rewrite during backfill (AC8 iii-v, issue #2030)', () => {
  const records = loadRawFixture(largestFixture).restHistory.messages

  /** Same record, new identity — stands in for events produced after the window was read. */
  const later = (record, i) => ({
    ...record, message_id: record.message_id && `${record.message_id}-later${i}`,
    tool_use_id: record.tool_use_id && `${record.tool_use_id}-later${i}`,
    timestamp: (record.timestamp || 0) + 1e6,
  })

  it('(iii) live events overlapping the window tail dedupe and extend it exactly like a full load', async () => {
    const extra = records.slice(-12).map(later)
    const expected = snapshot(await loadFull([...records, ...extra]))

    const store = await freshStore()
    const server = createFakeHistoryServer(records, { tailStart: records.length - 40, pageSize: 60 })
    serve(server)
    await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
    // Live frames: the last records of the window are re-delivered (cursor handoff overlap),
    // then genuinely new ones arrive, interleaved with backfill pages.
    for (const r of records.slice(-5)) store.applyRecord(SESSION_ID, r, 'live')
    for (const r of extra.slice(0, 6)) store.applyRecord(SESSION_ID, r, 'live')
    store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
    for (const r of extra.slice(6)) store.applyRecord(SESSION_ID, r, 'live')
    while (!store.isHistoryComplete(SESSION_ID)) {
      store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
    }
    expect(snapshot(store)).toEqual(expected)
  })

  it('(iv) resync with a gap smaller than the window keeps the loaded part and equals a full load', async () => {
    const extra = records.slice(-6).map(later)
    const grown = [...records, ...extra]
    const expected = snapshot(await loadFull(grown))

    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: records.length - 30, pageSize: 30 }))
    await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
    store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))

    // stream_reset: the fresh window covers the last 40 records of the grown history (overlaps).
    serve(createFakeHistoryServer(grown, { tailStart: grown.length - 40, pageSize: 30 }))
    const result = await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', resync: true, signal: new AbortController().signal })
    expect(result.replaced).toBe(false)
    while (!store.isHistoryComplete(SESSION_ID)) {
      store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
    }
    expect(snapshot(store)).toEqual(expected)
  })

  it('(iv) resync with a gap larger than the window replaces the suffix, restarts backfill, and converges', async () => {
    const extra = Array.from({ length: 80 }, (_, i) => later(records[i % records.length], i))
    const grown = [...records, ...extra]
    const expected = snapshot(await loadFull(grown))

    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: records.length - 20, pageSize: 40 }))
    await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })

    serve(createFakeHistoryServer(grown, { tailStart: grown.length - 20, pageSize: 40 }))
    const result = await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', resync: true, signal: new AbortController().signal })
    expect(result.replaced).toBe(true)
    while (!store.isHistoryComplete(SESSION_ID)) {
      store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
    }
    expect(snapshot(store)).toEqual(expected)
  })

  it('(v) history_id change mid-backfill restarts from the tail and still converges', async () => {
    const expected = snapshot(await loadFull(records))
    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: records.length - 20, pageSize: 40 }))
    await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
    store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))

    // The file is rewritten: same records, new history_id. The next page detects it...
    serve(createFakeHistoryServer(records, { tailStart: records.length - 20, pageSize: 40, historyId: 'h2' }))
    await expect(store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal })).rejects.toMatchObject({ isHistoryReset: true })
    // ...and the scheduler restarts from a fresh tail window.
    await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
    expect(store.getHistoryState(SESSION_ID).historyId).toBe('h2')
    while (!store.isHistoryComplete(SESSION_ID)) {
      store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
    }
    expect(snapshot(store)).toEqual(expected)
  })
})
