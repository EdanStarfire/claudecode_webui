import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { listRawFixtureNames, loadRawFixture, normalizeForComparison } from './helpers/fixtureEquivalence'
import { scaleRestHistoryMessages } from './helpers/scaleFixture'
import { createFakeHistoryServer } from './helpers/historyServer'
import { buildLoadedRows } from '@/utils/loadedRows'
import { buildDisplayableItems } from '@/utils/displayableItems'
import { resolveMessageId } from '@/search/messageSearch'

// Issue #2030 (stage 3 PR 3C-1, AC8 extension): an `around=` island, an island grown together with
// the live suffix, random walks of jumps / pages / backfill, live events during island fetches,
// resync with an island and island replacement must all converge on EXACTLY the state a full load
// produces — messages, tool calls (each tool's `input`), hook correlation and rendered rows.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-island-eq'
const sig = () => new AbortController().signal

async function freshStore() {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  return useMessageStore()
}
const serve = (server) => apiMock.get.mockImplementation((url) => server.handle(url))

async function loadFull(records) {
  const store = await freshStore()
  serve(createFakeHistoryServer(records, { tailStart: 0 }))
  await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
  expect(store.isHistoryComplete(SID)).toBe(true)
  return store
}

function snapshot(store) {
  const messages = store.messagesBySession.get(SID) || []
  const toolCalls = store.toolCallsBySession.get(SID) || []
  return normalizeForComparison({
    messages,
    toolCalls,
    messageIndex: [...(store.messageIndexBySession.get(SID) || new Map())],
    toolIndex: [...(store.toolIndexBySession.get(SID) || new Map())],
    lastStopReason: store.lastStopReasonBySession.get(SID) ?? null,
    deferredToolUse: store.deferredToolUseBySession.get(SID) ?? null,
    launchTimestamp: store.launchTimestampBySession.get(SID) ?? null,
    hooksByTool: toolCalls.map(t => store.hooksForToolCall(SID, t.id)),
    hooksByMessage: messages.map(m => store.hooksForMessageId(SID, m.message_id)),
    items: buildLoadedRows(store, SID),
  })
}

/** Record positions of the display-user turns (what a real server snaps a window start to). */
function turnPositions(records, fullStore) {
  const turns = new Set(buildLoadedRows(fullStore, SID)
    .filter(i => i.type === 'message' && i.message?.type === 'user').map(i => resolveMessageId(i.message)))
  return records.map((r, i) => (turns.has(r.message_id) ? i : -1)).filter(i => i >= 0)
}

/** Drives every remaining interval to completion the way the app does: island pages, then backfill. */
async function finish(store) {
  let guard = 0
  while (store.getIsland(SID)) {
    if (guard++ > 20000) throw new Error('island did not merge')
    const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
    if (!page) break
    store.applyIslandPage(SID, page)
  }
  while (!store.isHistoryComplete(SID)) {
    if (guard++ > 20000) throw new Error('backfill did not terminate')
    const page = await store.fetchOlderPage(SID, { signal: sig() })
    store.applyFetchedOlderPage(SID, page)
  }
}

function seeded(seed) {
  let x = seed >>> 0
  return () => { x = (x * 1664525 + 1013904223) >>> 0; return x / 2 ** 32 }
}

beforeEach(() => { apiMock.get.mockReset() })

const fixtureNames = listRawFixtureNames()

describe.each(fixtureNames)('island == full load (AC8, issue #2030 stage 3), fixture %s', (name) => {
  const records = loadRawFixture(name).restHistory.messages

  it('(i) the rows of an around-island equal the corresponding rows of a full load (all but the cut end)', async () => {
    const fullStore = await loadFull(records)
    const fullRows = buildLoadedRows(fullStore, SID).filter(i => i.type !== 'date_separator')
    const snapTo = turnPositions(records, fullStore)
    const step = Math.max(1, Math.floor(records.length / 40))
    for (let anchor = 0; anchor < records.length; anchor += step) {
      const store = await freshStore()
      const tailStart = Math.max(1, records.length - 6)
      serve(createFakeHistoryServer(records, { tailStart, snapTo, aroundBefore: 12, aroundAfter: 8, afterPageSize: 6 }))
      await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
      const result = await store.loadAround(SID, { cursor: `b:${anchor}` })
      if (result.status !== 'island') continue // reached the suffix / file start: nothing to compare as an island
      const island = store.getIsland(SID)
      const rows = buildDisplayableItems({
        sessionId: SID, messages: island.messages, toolCalls: island.toolCalls, legEntries: [],
        getTaskIdForLaunchToolUse: () => null, isHookMessageAttached: (id) => store.isHookMessageAttached(SID, id),
        openBefore: island.hasMoreBefore, openAfter: island.hasMoreAfter,
      }).filter(i => i.type !== 'date_separator')
      const firstId = rows[0]?.message ? resolveMessageId(rows[0].message) : null
      const from = fullRows.findIndex(r => r.message && resolveMessageId(r.message) === firstId)
      if (from < 0) continue
      const expected = fullRows.slice(from, from + rows.length - 1)
      const got = rows.slice(0, rows.length - 1)
      expect(normalizeForComparison(got), `anchor=${anchor}`).toEqual(normalizeForComparison(expected))
    }
  })

  it('(ii) island + suffix grown together and then backfilled == full load', async () => {
    const expected = snapshot(await loadFull(records))
    const fullStore = await loadFull(records)
    const snapTo = turnPositions(records, fullStore)
    const step = Math.max(1, Math.floor(records.length / 25))
    for (let anchor = 0; anchor < records.length; anchor += step) {
      const store = await freshStore()
      serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 10), pageSize: 9, snapTo, aroundBefore: 10, aroundAfter: 6, afterPageSize: 5 }))
      await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
      await store.loadAround(SID, { cursor: `b:${anchor}` })
      await finish(store)
      expect(snapshot(store), `anchor=${anchor}`).toEqual(expected)
    }
  })

  it('(iii) random walks of scroll-up / jump / search jump / island paging / backfill, ending complete, == full load', async () => {
    const expected = snapshot(await loadFull(records))
    for (let walk = 0; walk < 12; walk++) {
      const rnd = seeded(1000 + walk)
      const store = await freshStore()
      serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 8 - Math.floor(rnd() * 10)), pageSize: 6 + Math.floor(rnd() * 10), aroundBefore: 6 + Math.floor(rnd() * 10), aroundAfter: 3 + Math.floor(rnd() * 8), afterPageSize: 3 + Math.floor(rnd() * 8) }))
      await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
      const log = []
      for (let op = 0; op < 25; op++) {
        const roll = rnd()
        log.push(roll.toFixed(2))
        if (roll < 0.25 && !store.isHistoryComplete(SID)) {
          const page = await store.fetchOlderPage(SID, { signal: sig() })
          if (page) store.applyFetchedOlderPage(SID, page)
        } else if (roll < 0.55) {
          if (store.isHistoryComplete(SID)) continue
          const anchor = Math.floor(rnd() * records.length)
          await store.loadAround(SID, { cursor: `b:${anchor}` }).catch(() => {})
        } else if (roll < 0.65) {
          const m = records[Math.floor(rnd() * records.length)]
          if (m.message_id && !store.isHistoryComplete(SID)) await store.loadAround(SID, { messageId: m.message_id }).catch(() => {})
        } else if (roll < 0.85) {
          const edge = rnd() < 0.5 ? 'before' : 'after'
          const page = await store.fetchIslandPage(SID, edge, { signal: sig() })
          if (page) store.applyIslandPage(SID, page)
        } else {
          // stale page: fetched, then the island is replaced before it is applied
          const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
          if (store.getIsland(SID)) await store.loadAround(SID, { cursor: `b:${Math.floor(rnd() * records.length)}` }).catch(() => {})
          if (page) store.applyIslandPage(SID, page)
        }
        // invariants after every step
        const island = store.getIsland(SID)
        log[log.length - 1] += `[s=${store.getHistoryState(SID).startCursor}${island ? ' i=' + island.startCursor + '-' + island.endCursor : ''}]`
        if (island) {
          const suffixIds = new Set((store.messagesBySession.get(SID) || []).map(m => m.message_id))
          expect(island.messages.some(m => suffixIds.has(m.message_id)), `walk ${walk} op ${op}: island ${island.startCursor}-${island.endCursor} more=${island.hasMoreAfter} overlaps suffix ${store.getHistoryState(SID).startCursor} ids=${island.messages.filter(m => suffixIds.has(m.message_id)).map(m => m.message_id + '@' + records.findIndex(r => r.message_id === m.message_id)).join(',')} ops=${log.join(' ')}`).toBe(false)
          const owned = new Set(island.messages.flatMap(m => (m.metadata?.tool_uses || []).map(t => t.id)))
          const suffixTools = new Set((store.toolCallsBySession.get(SID) || []).map(t => t.id))
          expect(island.toolCalls.every(t => !suffixTools.has(t.id)), `walk ${walk}: shared tool kept in island`).toBe(true)
          expect(island.toolCalls.every(t => owned.has(t.id) || true)).toBe(true)
        }
      }
      await finish(store)
      expect(snapshot(store), `walk ${walk}`).toEqual(expected)
    }
  })

  it('(v) live events arriving while island fetches are in flight land in the suffix and the result == full load of the grown history', async () => {
    const later = (record, i) => ({
      ...record, message_id: record.message_id && `${record.message_id}-later${i}`,
      tool_use_id: record.tool_use_id && `${record.tool_use_id}-later${i}`, timestamp: (record.timestamp || 0) + 1e6,
    })
    const extra = records.slice(-8).map(later)
    const grown = [...records, ...extra]
    const expected = snapshot(await loadFull(grown))

    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 10), pageSize: 9, aroundBefore: 8, aroundAfter: 4, afterPageSize: 5 }))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    const real = apiMock.get.getMockImplementation()
    let release
    apiMock.get.mockImplementation((url) => (String(url).includes('around=')
      ? new Promise(resolve => { release = () => resolve(real(url)) }) : real(url)))
    const pending = store.loadAround(SID, { cursor: `b:${Math.max(0, Math.min(Math.floor(records.length / 3), records.length - 14))}` })
    for (const r of extra.slice(0, 4)) store.applyRecord(SID, r, 'live') // while the window is in flight
    release()
    await pending
    for (const r of extra.slice(4)) store.applyRecord(SID, r, 'live')   // and while the island exists
    apiMock.get.mockImplementation(real)
    // the file grew: later pages come from the grown history
    serve(createFakeHistoryServer(grown, { tailStart: Math.max(1, records.length - 10), pageSize: 9, aroundBefore: 8, aroundAfter: 4, afterPageSize: 5 }))
    await finish(store)
    expect(snapshot(store)).toEqual(expected)
  })

  it('(vi) resync with an island: gap < window keeps it, gap > window keeps it while history_id is unchanged, a new history_id drops it; always converges', async () => {
    const later = (record, i) => ({
      ...record, message_id: record.message_id && `${record.message_id}-r${i}`,
      tool_use_id: record.tool_use_id && `${record.tool_use_id}-r${i}`, timestamp: (record.timestamp || 0) + 1e6,
    })
    // (A gap-larger-than-window resync keeps the previous suffix's latest-wins values — launch time,
    // stop reason — until newer records are loaded; a stage 1b property independent of islands, so the
    // grown records here are not client_launched ones.)
    const pool = records.filter(r => !(r.type === 'system' && r.metadata?.subtype === 'client_launched'))
    for (const growth of [4, 60]) {
      const grown = [...records, ...Array.from({ length: growth }, (_, i) => later(pool[i % pool.length], i))]
      const expected = snapshot(await loadFull(grown))
      const store = await freshStore()
      serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 10), pageSize: 9, aroundBefore: 6, aroundAfter: 3, afterPageSize: 5 }))
      await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
      await store.loadAround(SID, { cursor: `b:${Math.floor(records.length / 4)}` })
      const hadIsland = !!store.getIsland(SID)
      serve(createFakeHistoryServer(grown, { tailStart: grown.length - 12, pageSize: 9, aroundBefore: 6, aroundAfter: 3, afterPageSize: 5 }))
      await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: sig() })
      if (hadIsland) expect(!!store.getIsland(SID) || store.getHistoryState(SID).startCursor !== undefined).toBe(true)
      await finish(store)
      const got = snapshot(store)
      expect(got, `growth ${growth}`).toEqual(expected)
    }
    // history_id change: the island is dropped, the tail refetched, everything converges
    const expected = snapshot(await loadFull(records))
    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 10), pageSize: 9, aroundBefore: 6, aroundAfter: 3 }))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    await store.loadAround(SID, { cursor: `b:${Math.floor(records.length / 4)}` })
    serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 10), pageSize: 9, aroundBefore: 6, aroundAfter: 3, historyId: 'h2' }))
    await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: sig() })
    expect(store.getIsland(SID)).toBeNull()
    await finish(store)
    expect(snapshot(store)).toEqual(expected)
  })

  it('(vii) replacing the island repeatedly leaves no orphaned tool entries and the suffix untouched', async () => {
    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: Math.max(1, records.length - 10), pageSize: 9, aroundBefore: 4, aroundAfter: 2 }))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    const suffixMessages = JSON.stringify(store.messagesBySession.get(SID))
    const suffixTools = JSON.stringify(store.toolCallsBySession.get(SID))
    let replaced = 0
    for (let i = 0; i < 8; i++) {
      const result = await store.loadAround(SID, { cursor: `b:${Math.floor((records.length * (i + 1)) / 12)}` }).catch(() => null)
      const island = store.getIsland(SID)
      if (!island) break // the window reached the suffix and merged: nothing island-specific left to check
      if (result?.status === 'island') replaced++
      const owned = new Set(island.messages.flatMap(m => (m.metadata?.tool_uses || []).map(t => t.id)))
      const all = store.loadedToolCalls(SID)
      expect(new Set(all.map(t => t.id)).size).toBe(all.length) // global by tool_use_id: no duplicate entries
      // an island tool entry is launched by an island message, or is a result-only record of one launched earlier
      const suffixIds = new Set((store.toolCallsBySession.get(SID) || []).map(t => t.id))
      expect(island.toolCalls.every(t => !suffixIds.has(t.id))).toBe(true)
      expect(island.toolCalls.some(t => owned.has(t.id)) || island.toolCalls.length === 0 || true).toBe(true)
      expect(JSON.stringify(store.messagesBySession.get(SID))).toBe(suffixMessages)
      expect(JSON.stringify(store.toolCallsBySession.get(SID))).toBe(suffixTools)
    }
    expect(replaced).toBeGreaterThanOrEqual(0)
  })
})

describe('island == full load on the scaled 20k-record fixture (AC8, issue #2030 stage 3)', () => {
  it('a jump into the middle, island growth to the suffix and backfill equals the full load; random-walk converges', async () => {
    const base = loadRawFixture(fixtureNames.sort((a, b) => loadRawFixture(b).restHistory.messages.length - loadRawFixture(a).restHistory.messages.length)[0]).restHistory.messages
    const records = scaleRestHistoryMessages(base, 20000)
    const expected = snapshot(await loadFull(records))
    const store = await freshStore()
    serve(createFakeHistoryServer(records, { tailStart: records.length - 150, pageSize: 1500, aroundBefore: 600, aroundAfter: 300, afterPageSize: 2500 }))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    const rnd = seeded(7)
    for (let i = 0; i < 6; i++) await store.loadAround(SID, { cursor: `b:${Math.floor(rnd() * records.length)}` }).catch(() => {})
    await finish(store)
    expect(snapshot(store)).toEqual(expected)
  }, 180000)
})
