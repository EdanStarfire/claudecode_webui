import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { listRawFixtureNames, loadRawFixture, normalizeForComparison } from './helpers/fixtureEquivalence'
import { scaleRestHistoryMessages } from './helpers/scaleFixture'
import { createFakeHistoryServer } from './helpers/historyServer'
import { buildDisplayableItems } from '@/utils/displayableItems'
import { resolveMessageId } from '@/search/messageSearch'
import { findTurnBefore, cursorOffset } from '@/utils/turnNavigation'
import fs from 'node:fs'
import path from 'node:path'
import { RAW_FIXTURES_ROOT } from './helpers/fixtureEquivalence'

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

// ---------------------------------------------------------------------------------------------
// Issue #2030 (stage 2 PR B, AC8 extension): a PARTIAL window plus the server history index must
// describe the same session a full load does — the same turns (loaded rows + index turns older
// than the window), the same compaction groups (keyed by first-record message_id) with the same
// PreCompact hook attachment, the same rows for what is loaded — and a jump that extends the
// suffix to an unloaded turn must leave state == the full-load state once the backfill finishes.
// The index fed to the fake server here is derived from the FULL load; the server's own
// reducer is pinned to that derivation by historyIndexEquivalence.test.js / test_history_index.py.
// ---------------------------------------------------------------------------------------------

const CASES = JSON.parse(fs.readFileSync(path.join(RAW_FIXTURES_ROOT, '..', 'history_index_cases.json'), 'utf-8')).cases

function displayItems(store) {
  return buildDisplayableItems({
    sessionId: SESSION_ID,
    messages: store.messagesBySession.get(SESSION_ID) || [],
    toolCalls: store.toolCallsBySession.get(SESSION_ID) || [],
    legEntries: store.allTaskLegEntriesForSession(SESSION_ID),
    getTaskIdForLaunchToolUse: store.getTaskIdForLaunchToolUse,
    isHookMessageAttached: (id) => store.isHookMessageAttached(SESSION_ID, id),
    historyComplete: store.isHistoryComplete(SESSION_ID),
  })
}

const turnIdsOf = (items) => items.filter(i => i.type === 'message' && i.message?.type === 'user').map(i => resolveMessageId(i.message))
const groupsOf = (store, items) => items.filter(i => i.type === 'compaction').map(i => ({
  key: i.groupKey,
  hooks: store.hooksForCompaction(SESSION_ID, i.groupKey),
}))
const nonDate = (items) => items.filter(i => i.type !== 'date_separator')

/** Index derived from a full load: turn ids, compaction keys, and each turn's record position. */
async function deriveIndex(records) {
  const store = await loadFull(records)
  const items = displayItems(store)
  const turns = turnIdsOf(items)
  return { turns, compactions: items.filter(i => i.type === 'compaction').map(i => i.groupKey), full: { store, items } }
}

/** Window start positions a real (snapped) server could pick: a turn record or right after a group. */
function snapStarts(records, turns, step = 1) {
  const position = new Map(records.map((r, i) => [r.message_id, i]))
  const starts = turns.map(id => position.get(id)).filter(i => i > 0)
  return starts.filter((_, i) => i % step === 0)
}

async function partialWithIndex(records, index, tailStart, extra = {}) {
  const store = await freshStore()
  const server = createFakeHistoryServer(records, { tailStart, pageSize: 7, index: { turns: index.turns, compactions: index.compactions }, ...extra })
  serve(server)
  await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
  await store.ensureHistoryIndex(SESSION_ID)
  return { store, server }
}

function checkPartialAgainstFull({ store, tailStart, records, index }) {
  const partial = displayItems(store)
  const entry = store.getHistoryIndex(SESSION_ID)
  expect(entry.status).toBe('ready')

  // (a) turns: index turns older than the loaded start ++ loaded turn rows == every turn
  const startOffset = cursorOffset(store.getHistoryState(SESSION_ID).startCursor)
  expect(startOffset).toBe(tailStart)
  const older = entry.turns.filter(t => t[2] < startOffset).map(t => t[1])
  expect(findTurnBefore(entry.turns, startOffset)?.messageId ?? null).toBe(older.at(-1) ?? null)
  expect([...older, ...turnIdsOf(partial)]).toEqual(index.turns)

  // (b) compaction groups in the window: same key + same PreCompact attachment as the full load
  const full = index.full
  const position = new Map(records.map((r, i) => [r.message_id, i]))
  const fullGroups = groupsOf(full.store, full.items).filter(g => position.get(g.key) >= tailStart)
  expect(groupsOf(store, partial)).toEqual(fullGroups)

  // (c) the loaded rows are the full load's newest rows
  const fullRows = nonDate(full.items)
  const partialRows = nonDate(partial)
  expect(normalizeForComparison(partialRows)).toEqual(normalizeForComparison(fullRows.slice(fullRows.length - partialRows.length)))
}

describe('partial window + history index == full load (AC8, issue #2030 stage 2)', () => {
  describe.each(fixtureNames)('fixture %s', (name) => {
    const records = loadRawFixture(name).restHistory.messages

    it('at every snappable window start', async () => {
      const index = await deriveIndex(records)
      for (const tailStart of snapStarts(records, index.turns)) {
        const { store } = await partialWithIndex(records, index, tailStart)
        checkPartialAgainstFull({ store, tailStart, records, index })
      }
    })
  })

  it.each(CASES.filter(c => !c.archive).map(c => [c.name, c]))('shared case %s', async (_name, c) => {
    const records = c.records
    const index = await deriveIndex(records)
    expect(index.turns).toEqual(c.expected.turns)
    expect(index.compactions).toEqual(c.expected.compactions)
    for (const tailStart of snapStarts(records, index.turns)) {
      const { store } = await partialWithIndex(records, index, tailStart)
      checkPartialAgainstFull({ store, tailStart, records, index })
    }
  })

  it('PreCompact hooks attach to the right group on a window that starts between groups (hook fixture shape)', async () => {
    const sys = (id, metadata) => ({ type: 'system', message_id: id, content: '', timestamp: 1, metadata })
    // The hook records follow the group (hooks between a group's records would break its pattern).
    const group = (p) => [
      sys(`${p}-c`, { subtype: 'status', init_data: { status: 'compacting' } }),
      sys(`${p}-n`, { subtype: 'status', init_data: { status: null } }),
      sys(`${p}-b`, { subtype: 'compact_boundary' }),
      { type: 'user', message_id: `${p}-cont`, content: 'This session is being continued from a previous conversation that ran out of context.', timestamp: 2, metadata: {} },
      sys(`${p}-hs`, { subtype: 'hook_started', init_data: { hook_id: `${p}-h`, hook_event: 'PreCompact', hook_name: 'hk' } }),
      sys(`${p}-hr`, { subtype: 'hook_response', init_data: { hook_id: `${p}-h`, hook_event: 'PreCompact', hook_name: 'hk', exit_code: 0 } }),
    ]
    const u = (id) => ({ type: 'user', message_id: id, content: id, timestamp: 3, metadata: {} })
    const records = [u('u1'), ...group('g1'), u('u2'), ...group('g2'), u('u3'), ...group('g3'), u('u4')]
    const index = await deriveIndex(records)
    expect(index.compactions).toEqual(['g1-c', 'g2-c', 'g3-c'])
    for (const tailStart of snapStarts(records, index.turns)) {
      const { store } = await partialWithIndex(records, index, tailStart)
      checkPartialAgainstFull({ store, tailStart, records, index })
    }
    const { store } = await partialWithIndex(records, index, records.findIndex(r => r.message_id === 'u3'))
    expect(store.hooksForCompaction(SESSION_ID, 'g3-c')).toHaveLength(1)
    expect(store.hooksForCompaction(SESSION_ID, 'g2-c')).toEqual([])
  })

  it('scaled 20k-record fixture: groups and turns match at a deep tail window', async () => {
    const base = loadRawFixture(largestFixture).restHistory.messages
    const records = scaleRestHistoryMessages(base, 20000)
    const index = await deriveIndex(records)
    const starts = snapStarts(records, index.turns)
    const tailStart = starts[starts.length - 3]
    const { store } = await partialWithIndex(records, index, tailStart)
    checkPartialAgainstFull({ store, tailStart, records, index })
  }, 120000)

  describe('jump to an unloaded turn', () => {
    it('extending the suffix until the target is loaded, then finishing the backfill, equals a full load', async () => {
      const records = loadRawFixture(largestFixture).restHistory.messages
      const index = await deriveIndex(records)
      const expected = snapshot(index.full.store)
      const tailStart = snapStarts(records, index.turns).at(-1)
      const { store } = await partialWithIndex(records, index, tailStart)

      const target = findTurnBefore(store.getHistoryIndex(SESSION_ID).turns, cursorOffset(store.getHistoryState(SESSION_ID).startCursor))
      expect(target).toBeTruthy()
      let pages = 0
      while (!store.isMessageLoaded(SESSION_ID, target.messageId)) {
        pages++
        store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal, maxBytes: 512 * 1024 }))
      }
      expect(pages).toBeGreaterThan(0)
      // the target is now a loaded displayable row, and what is loaded is a valid suffix
      expect(turnIdsOf(displayItems(store))).toContain(target.messageId)
      while (!store.isHistoryComplete(SESSION_ID)) {
        store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
      }
      expect(snapshot(store)).toEqual(expected)
    })
  })

  describe('index lost / stale / old server', () => {
    it('a 404 leaves loading and backfill untouched (Prev stays locked until complete — covered in the status bar test)', async () => {
      const records = loadRawFixture(largestFixture).restHistory.messages
      const expected = snapshot(await loadFull(records))
      const store = await freshStore()
      const server = createFakeHistoryServer(records, { tailStart: records.length - 20, pageSize: 40, index: null })
      serve(server)
      await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
      expect((await store.ensureHistoryIndex(SESSION_ID)).status).toBe('unavailable')
      while (!store.isHistoryComplete(SESSION_ID)) {
        store.applyFetchedOlderPage(SESSION_ID, await store.fetchOlderPage(SESSION_ID, { signal: new AbortController().signal }))
      }
      expect(snapshot(store)).toEqual(expected)
    })

    it('a history_id change drops the index; it is refetched for the new history', async () => {
      const records = loadRawFixture(largestFixture).restHistory.messages
      const index = await deriveIndex(records)
      const { store } = await partialWithIndex(records, index, snapStarts(records, index.turns).at(-1))
      expect(store.getHistoryIndex(SESSION_ID).historyId).toBe('h1')
      serve(createFakeHistoryServer(records, { tailStart: records.length - 20, historyId: 'h2', index: { turns: index.turns, compactions: index.compactions } }))
      await store.loadMessages(SESSION_ID, null, 0, { window: 'tail', signal: new AbortController().signal })
      expect(store.getHistoryIndex(SESSION_ID)).toBeNull()
      await store.ensureHistoryIndex(SESSION_ID)
      expect(store.getHistoryIndex(SESSION_ID)).toMatchObject({ status: 'ready', historyId: 'h2' })
    })
  })
})
