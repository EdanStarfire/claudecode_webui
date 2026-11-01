import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { effectScope, computed } from 'vue'
import { createFakeHistoryServer } from './helpers/historyServer'

// Issue #2030 (stage 3 PR 3C-1): the interval model — a pinned island next to the live suffix,
// the `loadAround` jump path, island growth, merge-on-touch, replacement, history_id changes.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-island'
const TURNS = 60 // 3 records per turn: user, assistant (one tool), tool_call

function buildRecords(turns = TURNS) {
  const records = []
  for (let t = 0; t < turns; t++) {
    records.push({ type: 'user', message_id: `u${t}`, timestamp: 1000 + t * 10, content: `turn ${t}`, metadata: {} })
    records.push({
      type: 'assistant', message_id: `a${t}`, timestamp: 1001 + t * 10, content: `answer ${t}`,
      metadata: { turn_id: `turn-${t}`, has_tool_uses: true, tool_uses: [{ id: `tool${t}`, name: 'Bash', input: { command: `echo ${t}` } }] },
    })
    records.push({
      type: 'tool_call', message_id: `tc${t}`, tool_use_id: `tool${t}`, name: 'Bash', status: 'completed',
      input: { command: `echo ${t}` }, result: `out ${t}`, turn_id: `turn-${t}`, created_at: 1002 + t * 10,
    })
  }
  return records
}

async function setup(records = buildRecords(), options = {}) {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const store = useMessageStore()
  const server = createFakeHistoryServer(records, { tailStart: records.length - 12, ...options })
  apiMock.get.mockImplementation((url) => server.handle(url))
  await store.loadMessages(SID, null, 0, { window: 'tail', signal: new AbortController().signal })
  return { store, server, records }
}

const ids = (list) => list.map(m => m.message_id)
const sig = () => new AbortController().signal

beforeEach(() => {
  apiMock.get.mockReset()
})

describe('no island: the suffix is the hot path, untouched', () => {
  it('loaded* getters return the suffix arrays by identity', async () => {
    const { store } = await setup()
    expect(store.getIsland(SID)).toBeNull()
    expect(store.loadedMessages(SID)).toBe(store.messagesBySession.get(SID))
    expect(store.loadedToolCalls(SID)).toBe(store.toolCallsBySession.get(SID))
  })

  it('a computed over loadedMessages re-runs once per suffix append and keeps the array identity', async () => {
    const { store } = await setup()
    const scope = effectScope()
    let runs = 0
    const arrays = []
    const list = scope.run(() => computed(() => { runs++; const m = store.loadedMessages(SID); arrays.push(m); return m }))
    expect(list.value).toBe(store.messagesBySession.get(SID))
    const before = runs
    store.applyRecord(SID, { type: 'user', message_id: 'live1', content: 'hi', timestamp: 5000, metadata: {} }, 'live')
    expect(list.value.at(-1).message_id).toBe('live1')
    expect(runs - before).toBe(1)
    expect(list.value).toBe(store.messagesBySession.get(SID))
    scope.stop()
  })
})

describe('loadAround', () => {
  it('installs an island away from the suffix without touching the suffix', async () => {
    const { store, server } = await setup()
    const suffixBefore = [...store.messagesBySession.get(SID)]
    const result = await store.loadAround(SID, { cursor: 'b:60' })
    expect(result.status).toBe('island')
    expect(server.requests.at(-1)).toContain('around=b%3A60')
    const island = store.getIsland(SID)
    expect(island).toMatchObject({ historyId: 'h1', hasMoreBefore: true, hasMoreAfter: true })
    expect(island.messages.some(m => m.message_id === 'u20')).toBe(true) // record 60 = turn 20
    expect(store.messagesBySession.get(SID)).toEqual(suffixBefore)
    expect(store.isMessageLoaded(SID, 'u20')).toBe(true)
    expect(store.isHistoryComplete(SID)).toBe(false)
    // union getters: island ++ suffix
    expect(ids(store.loadedMessages(SID)).slice(-3)).toEqual(ids(suffixBefore).slice(-3))
    expect(store.loadedMessages(SID).length).toBe(island.messages.length + suffixBefore.length)
    expect(store.findLoadedToolCall(SID, 'tool20')).toBeTruthy()
  })

  it('uses around_id for a message id / tool_use id', async () => {
    const { store, server } = await setup()
    await store.loadAround(SID, { messageId: 'u10' })
    expect(server.requests.at(-1)).toContain('around_id=u10')
    await store.loadAround(SID, { toolUseId: 'tool30' })
    expect(server.requests.at(-1)).toContain('around_id=tool30')
    expect(store.isMessageLoaded(SID, 'a30')).toBe(true)
  })

  it('a target already loaded resolves without a request', async () => {
    const { store, server } = await setup()
    const n = server.requests.length
    expect(await store.loadAround(SID, { messageId: 'u59' })).toEqual({ status: 'loaded' })
    expect(server.requests.length).toBe(n)
  })

  it('a fully loaded session needs no fetch', async () => {
    const { store, server } = await setup(buildRecords(3), { tailStart: 0 })
    const n = server.requests.length
    expect((await store.loadAround(SID, { cursor: 'b:3' })).status).toBe('complete')
    expect(server.requests.length).toBe(n)
  })

  it('maps 404 message_not_found, 409 and 503', async () => {
    const { store } = await setup()
    await expect(store.loadAround(SID, { messageId: 'nope' })).rejects.toMatchObject({ reason: 'not-found' })
    await expect(store.loadAround(SID, { cursor: 'not-a-cursor' })).rejects.toMatchObject({ reason: 'reset', isHistoryReset: true })
    const real = apiMock.get.getMockImplementation()
    apiMock.get.mockImplementation(() => Promise.reject(Object.assign(new Error('resolve_busy'), { status: 503 })))
    await expect(store.loadAround(SID, { messageId: 'u1' })).rejects.toMatchObject({ reason: 'busy' })
    apiMock.get.mockImplementation(real)
    expect(store.getIsland(SID)).toBeNull()
  })

  it('a window that reaches the suffix merges into it at once (complete when it reaches the file start)', async () => {
    const { store } = await setup(buildRecords(), { tailStart: 150, aroundAfter: 30 })
    const result = await store.loadAround(SID, { cursor: 'b:140' })
    expect(result.status).toBe('merged')
    expect(store.getIsland(SID)).toBeNull()
    expect(store.getHistoryState(SID).startCursor).toMatch(/^b:/)
    const messages = store.messagesBySession.get(SID)
    expect(new Set(ids(messages)).size).toBe(messages.length) // no duplicates
    expect(ids(messages).indexOf('u46')).toBeLessThan(ids(messages).indexOf('u50'))
  })

  it('history_id change in the window response drops the island and rejects with reset', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:60' })
    const real = apiMock.get.getMockImplementation()
    apiMock.get.mockImplementation(async (url) => ({ ...(await real(url)), history_id: 'h2' }))
    await expect(store.loadAround(SID, { cursor: 'b:30' })).rejects.toMatchObject({ reason: 'reset' })
    expect(store.getIsland(SID)).toBeNull()
  })
})

describe('replace island', () => {
  it('a second jump replaces the island: its messages and exclusively-owned tools are gone, the suffix is unchanged', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:60' })
    expect(store.findLoadedToolCall(SID, 'tool20')).toBeTruthy()
    const suffix = [...store.messagesBySession.get(SID)]
    const suffixTools = [...store.toolCallsBySession.get(SID)]
    await store.loadAround(SID, { cursor: 'b:21' })
    expect(store.isMessageLoaded(SID, 'u20')).toBe(false)
    expect(store.findLoadedToolCall(SID, 'tool20')).toBeUndefined()
    expect(store.isMessageLoaded(SID, 'u7')).toBe(true)
    expect(store.messagesBySession.get(SID)).toEqual(suffix)
    expect(store.toolCallsBySession.get(SID)).toEqual(suffixTools)
    // no orphan tool entries: every island tool belongs to an island message
    const island = store.getIsland(SID)
    const owned = new Set(island.messages.flatMap(m => (m.metadata?.tool_uses || []).map(t => t.id)))
    expect(island.toolCalls.every(t => owned.has(t.id))).toBe(true)
  })

  it('a jump while the first is mid-fetch aborts it: only the second lands, no flicker of the suffix', async () => {
    const { store, server } = await setup()
    const real = apiMock.get.getMockImplementation()
    let releaseFirst
    apiMock.get.mockImplementation((url, options) => {
      if (String(url).includes('around=b%3A60')) {
        return new Promise((resolve, reject) => {
          releaseFirst = () => resolve(real(url))
          options?.signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
        })
      }
      return real(url)
    })
    const suffix = store.messagesBySession.get(SID)
    const suffixLength = suffix.length
    const first = store.loadAround(SID, { cursor: 'b:60' })
    const firstOutcome = first.then(() => 'landed', (err) => err.reason)
    const second = await store.loadAround(SID, { cursor: 'b:21' })
    expect(second.status).toBe('island')
    releaseFirst?.()
    expect(await firstOutcome).toBe('superseded')
    expect(store.isMessageLoaded(SID, 'u7')).toBe(true)
    expect(store.isMessageLoaded(SID, 'u20')).toBe(false)
    expect(store.messagesBySession.get(SID)).toHaveLength(suffixLength)
    expect(server.requests.filter(u => u.includes('around=')).length).toBe(2)
  })

  it('a tool the suffix already holds stays the suffix\'s: the island fills what is absent and does not keep a copy', async () => {
    const records = buildRecords()
    const { store } = await setup(records, { tailStart: 150, aroundAfter: 8, aroundBefore: 12 })
    // the suffix saw only the later tool_call record of tool50 (pre-streaming input is empty there)
    store.applyRecord(SID, { type: 'tool_call', message_id: 'x1', tool_use_id: 'tool44', name: 'Bash', status: 'completed', input: {}, turn_id: 'turn-44', created_at: 2 }, 'live')
    const stub = store.toolCallsBySession.get(SID).find(t => t.id === 'tool44')
    const result = await store.loadAround(SID, { cursor: 'b:134' })
    expect(result.status).toBe('island')
    const island = store.getIsland(SID)
    expect(island.toolCalls.some(t => t.id === 'tool44')).toBe(false)
    expect(store.findLoadedToolCall(SID, 'tool44')).toBe(store.toolCallsBySession.get(SID).find(t => t.id === 'tool44'))
    expect(stub.input).toEqual({ command: 'echo 44' }) // filled from the island's record
  })
})

describe('island growth and merge', () => {
  it('after pages grow the island and merge into the suffix when they reach it (no duplicates, no loss)', async () => {
    const { store, records } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    let guard = 0
    while (store.getIsland(SID)) {
      if (guard++ > 100) throw new Error('did not merge')
      const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
      expect(page).toBeTruthy()
      expect(store.applyIslandPage(SID, page)).toBe(true)
    }
    const messages = store.messagesBySession.get(SID)
    expect(new Set(ids(messages)).size).toBe(messages.length)
    // contiguous: every message record of [island start .. end] is present exactly once, in order
    const expected = records.filter(r => r.type !== 'tool_call').map(r => r.message_id)
    const got = ids(messages)
    const from = expected.indexOf(got[0])
    expect(got).toEqual(expected.slice(from))
    expect(store.getHistoryState(SID).startCursor).toBeDefined()
    expect(store.getHistoryState(SID).hasMoreBefore).toBe(true)
  })

  it('after requests are bounded by `until` = the suffix start', async () => {
    const { store, server } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    await store.fetchIslandPage(SID, 'after', { signal: sig() })
    const url = server.requests.at(-1)
    expect(url).toContain('after=')
    expect(url).toContain(`until=${encodeURIComponent(store.getHistoryState(SID).startCursor)}`)
  })

  it('before pages grow the island upward; reaching the file start sets hasMoreBefore=false', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    let guard = 0
    while (store.getIsland(SID).hasMoreBefore) {
      if (guard++ > 100) throw new Error('did not finish')
      const page = await store.fetchIslandPage(SID, 'before', { signal: sig() })
      expect(store.applyIslandPage(SID, page)).toBe(true)
    }
    const island = store.getIsland(SID)
    expect(island.messages[0].message_id).toBe('u0')
    expect(new Set(ids(island.messages)).size).toBe(island.messages.length)
    expect(store.isHistoryComplete(SID)).toBe(false) // the suffix still has an unloaded gap above it
  })

  it('a stale page (island replaced while it was in flight) is dropped', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
    await store.loadAround(SID, { cursor: 'b:90' })
    expect(store.applyIslandPage(SID, page)).toBe(false)
  })
})

describe('review fixes', () => {
  it('a background merge of the old island does not abort a newer in-flight jump', async () => {
    const { store } = await setup(buildRecords(), { tailStart: 150, aroundBefore: 6, aroundAfter: 6 })
    await store.loadAround(SID, { cursor: 'b:100' })
    const real = apiMock.get.getMockImplementation()
    let release
    apiMock.get.mockImplementation((url) => (String(url).includes('around=b%3A30')
      ? new Promise(resolve => { release = () => resolve(real(url)) }) : real(url)))
    const jump = store.loadAround(SID, { cursor: 'b:30' })
    // backfill reaches the old island: they merge while the jump is in flight
    let guard = 0
    while (store.getIsland(SID) && guard++ < 30) {
      const page = await store.fetchOlderPage(SID, { signal: sig() }).catch(() => null)
      if (page) store.applyFetchedOlderPage(SID, page)
      else break
    }
    release()
    await expect(jump).resolves.toMatchObject({ status: expect.stringMatching(/island|merged/) })
  })
})

describe('live events and resync', () => {
  it('live events while an island exists (or is being fetched) land in the suffix only, deduped by id', async () => {
    const { store } = await setup()
    const real = apiMock.get.getMockImplementation()
    let release
    apiMock.get.mockImplementation((url) => (String(url).includes('around=')
      ? new Promise(resolve => { release = () => resolve(real(url)) })
      : real(url)))
    const pending = store.loadAround(SID, { cursor: 'b:30' })
    const live = { type: 'assistant', message_id: 'live-a', content: 'streamed', timestamp: 9000, metadata: {} }
    store.applyRecord(SID, live, 'live')
    store.applyRecord(SID, live, 'live') // redelivery
    release()
    await pending
    store.applyRecord(SID, { type: 'user', message_id: 'live-b', content: 'more', timestamp: 9001, metadata: {} }, 'live')
    expect(ids(store.messagesBySession.get(SID)).filter(i => i.startsWith('live-'))).toEqual(['live-a', 'live-b'])
    expect(ids(store.getIsland(SID).messages).some(i => i.startsWith('live-'))).toBe(false)
  })

  it('a resync (tail window merged onto the suffix) keeps the island while history_id is unchanged', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    const islandBefore = ids(store.getIsland(SID).messages)
    await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: sig() })
    expect(ids(store.getIsland(SID).messages)).toEqual(islandBefore)
    expect(store.getHistoryState(SID).hasMoreBefore).toBe(true)
  })

  it('a resync that replaces the suffix (gap larger than the window) keeps a non-touching island', async () => {
    const records = buildRecords()
    const { store } = await setup(records)
    await store.loadAround(SID, { cursor: 'b:30' })
    const islandBefore = ids(store.getIsland(SID).messages)
    // the file grew far beyond the old window: the new tail window does not overlap the suffix
    const more = buildRecords(TURNS + 40).slice(records.length).map(r => ({ ...r, message_id: `n-${r.message_id}`, tool_use_id: r.tool_use_id && `n-${r.tool_use_id}` }))
    records.push(...more)
    const real = apiMock.get.getMockImplementation()
    apiMock.get.mockImplementation(async (url) => {
      const data = await real(url)
      return String(url).includes('window=tail') ? { ...data, messages: records.slice(records.length - 12), start_cursor: `b:${records.length - 12}`, total_bytes: records.length } : data
    })
    await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: sig() })
    expect(ids(store.getIsland(SID).messages)).toEqual(islandBefore)
    expect(store.getHistoryState(SID).startCursor).toBe(`b:${records.length - 12}`)
  })

  it('a changed history_id drops the island and replaces the suffix with a fresh tail', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    const real = apiMock.get.getMockImplementation()
    apiMock.get.mockImplementation(async (url) => ({ ...(await real(url)), history_id: 'h2' }))
    await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: sig() })
    expect(store.getIsland(SID)).toBeNull()
    expect(store.getHistoryState(SID).historyId).toBe('h2')
  })

  it('clearing the session drops the island', async () => {
    const { store } = await setup()
    await store.loadAround(SID, { cursor: 'b:30' })
    store.clearMessages(SID)
    expect(store.getIsland(SID)).toBeNull()
    expect(store.loadedMessages(SID)).toEqual([])
  })
})

describe('archive and hooks', () => {
  it('an archive session uses the archive URL for its island', async () => {
    setActivePinia(createPinia())
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const records = buildRecords()
    const server = createFakeHistoryServer(records, { tailStart: records.length - 12 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    const base = '/api/projects/p/archives/a/1/messages'
    await store.loadMessages(SID, null, 0, { window: 'tail', source: 'archive', baseUrl: base, signal: sig() })
    await store.loadAround(SID, { cursor: 'b:60' })
    expect(server.requests.at(-1).startsWith(`${base}?around=`)).toBe(true)
    expect(store.getIsland(SID).source).toBe('archive')
    expect(store.getIsland(SID).messages.length).toBeGreaterThan(0)
  })

  it('latest-wins state of the suffix is not overwritten by the island replay', async () => {
    const records = buildRecords()
    const { store } = await setup(records)
    store.lastStopReasonBySession.set(SID, 'end_turn')
    const before = store.lastStopReasonBySession.get(SID)
    await store.loadAround(SID, { cursor: 'b:60' })
    expect(store.lastStopReasonBySession.get(SID)).toBe(before)
  })
})
