import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { createFakeHistoryServer } from './helpers/historyServer'

// Issue #2030 (stage 1b): tail-first loading, older-page prepend-merge, resync, gating flags.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-hist'
let n = 0

function msg(type, extra = {}) {
  n += 1
  return { type, message_id: `m${n}`, timestamp: 1000 + n, content: `${type} ${n}`, metadata: {}, ...extra }
}
const user = (extra) => msg('user', extra)
const assistant = (extra) => msg('assistant', { metadata: { turn_id: `t${n}` }, ...extra })
function toolRecord(id, status, input, extra = {}) {
  n += 1
  return { type: 'tool_call', message_id: `tc${n}`, tool_use_id: id, name: 'Write', status, input, turn_id: 'turn-1', created_at: 500, ...extra }
}

async function setup() {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const { useSessionStore } = await import('@/stores/session')
  return { store: useMessageStore(), sessionStore: useSessionStore() }
}

function serve(records, options) {
  const server = createFakeHistoryServer(records, options)
  apiMock.get.mockImplementation((url) => server.handle(url))
  return server
}

const signalOf = () => new AbortController().signal

beforeEach(() => {
  apiMock.get.mockReset()
  n = 0
})

describe('loadHistoryTail', () => {
  it('installs the window, records the history state and the event cursor, stores latest_init', async () => {
    const { store, sessionStore } = await setup()
    const records = Array.from({ length: 10 }, (_, i) => (i % 2 ? assistant() : user()))
    serve(records, { tailStart: 6, latestInit: { model: 'm', slash_commands: ['/x'] }, eventCursor: 42 })
    const result = await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })

    expect(store.messagesBySession.get(SID)).toHaveLength(4)
    expect(result.hasMore).toBe(true)
    expect(store.isHistoryComplete(SID)).toBe(false)
    expect(store.getHistoryState(SID)).toMatchObject({ startCursor: 'b:6', hasMoreBefore: true, historyId: 'h1', baseUrl: `/api/sessions/${SID}/messages` })
    expect(store.loadedEventCursors.get(SID)).toBe(42)
    expect(sessionStore.initData.get(SID)).toMatchObject({ model: 'm' })
    expect(apiMock.get.mock.calls[0][0]).toBe(`/api/sessions/${SID}/messages?window=tail`)
  })

  it('a session shorter than one window is complete immediately (behaves like a full load)', async () => {
    const { store } = await setup()
    serve([user(), assistant()], { tailStart: 0 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    expect(store.isHistoryComplete(SID)).toBe(true)
    expect(store.historyProgress(SID).fraction).toBe(1)
  })

  it('reports backfill progress from the cursor', async () => {
    const { store } = await setup()
    serve(Array.from({ length: 10 }, () => user()), { tailStart: 5 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    // fake server: one byte per record, cursor b:5 of 10 -> 5 loaded
    expect(store.historyProgress(SID)).toMatchObject({ fraction: 0.5, loadedBytes: 5, totalBytes: 10 })
  })

  it('degrades to "no percentage" when the cursor is not the file reader\'s b:<offset> form', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 10 }, () => user())
    const server = createFakeHistoryServer(records, { tailStart: 5 })
    apiMock.get.mockImplementation(async (url) => (String(url).includes('before=')
      ? { messages: [], start_cursor: 'row:0', has_more_before: false, history_id: 'h1' }
      : { ...(await server.handle(url)), start_cursor: 'row:5' }))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    expect(store.isHistoryComplete(SID)).toBe(false) // still tracked and backfillable
    expect(store.historyProgress(SID)).toMatchObject({ fraction: null, loadedBytes: null })
    // The page request still passes the opaque cursor through untouched.
    await store.fetchOlderPage(SID, { signal: signalOf() })
    expect(apiMock.get.mock.calls.at(-1)[0]).toContain('before=row%3A5')
  })

  it('does not touch the shared hydration tracker for an archive view and uses the archive URL', async () => {
    const { store } = await setup()
    const live = store.claimHydrationLoad('live-session')
    serve([user(), assistant()], { tailStart: 0 })
    await store.loadMessages(SID, null, 0, {
      window: 'tail', source: 'archive', baseUrl: '/api/projects/p/archives/a/1/messages', signal: signalOf(),
    })
    expect(apiMock.get.mock.calls[0][0]).toBe('/api/projects/p/archives/a/1/messages?window=tail')
    expect(store.getHistoryState(SID).baseUrl).toBe('/api/projects/p/archives/a/1/messages')
    expect(live.signal.aborted).toBe(false)
  })

  it('aborts when the caller\'s signal fires while the window is in flight', async () => {
    const { store } = await setup()
    const controller = new AbortController()
    apiMock.get.mockImplementation(() => new Promise((_resolve, reject) => {
      controller.signal.addEventListener('abort', () => reject(new Error('aborted')))
    }))
    const pending = store.loadMessages(SID, null, 0, { window: 'tail', signal: controller.signal })
    controller.abort()
    await expect(pending).rejects.toThrow()
    expect(store.messagesBySession.has(SID)).toBe(false)
  })
})

describe('older pages', () => {
  it('fetches with low priority, the page budget and the opaque cursor', async () => {
    const { store } = await setup()
    serve(Array.from({ length: 10 }, () => user()), { tailStart: 6, pageSize: 4 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    const page = await store.fetchOlderPage(SID, { signal: signalOf(), maxBytes: 262144 })
    const [url, options] = apiMock.get.mock.calls.at(-1)
    expect(url).toBe(`/api/sessions/${SID}/messages?before=b%3A6&max_bytes=262144`)
    expect(options.priority).toBe('low')
    expect(page.records).toHaveLength(4)
    expect(store.applyFetchedOlderPage(SID, page)).toBe(true)
    expect(store.getHistoryState(SID).startCursor).toBe('b:2')
    expect(store.messagesBySession.get(SID).map(m => m.message_id)).toEqual(['m3', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9', 'm10'])
  })

  it('flags a 409 (cursor_invalid) and a history_id change as a history reset', async () => {
    const { store } = await setup()
    serve(Array.from({ length: 10 }, () => user()), { tailStart: 6 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })

    apiMock.get.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 409 }))
    await expect(store.fetchOlderPage(SID, { signal: signalOf() })).rejects.toMatchObject({ isHistoryReset: true })

    const server = createFakeHistoryServer(Array.from({ length: 10 }, () => user()), { tailStart: 6, historyId: 'rewritten' })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await expect(store.fetchOlderPage(SID, { signal: signalOf() })).rejects.toMatchObject({ isHistoryReset: true })
  })

  it('drops a fetched page when the loaded suffix moved on while it was in flight', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 10 }, () => user())
    serve(records, { tailStart: 6 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    const stale = await store.fetchOlderPage(SID, { signal: signalOf() })
    // A resync replaced the suffix (generation bump) before the page was applied.
    serve(records, { tailStart: 8 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    expect(store.applyFetchedOlderPage(SID, stale)).toBe(false)
    expect(store.messagesBySession.get(SID)).toHaveLength(2)
  })

  it('returns null when nothing older remains', async () => {
    const { store } = await setup()
    serve([user()], { tailStart: 0 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    expect(await store.fetchOlderPage(SID, { signal: signalOf() })).toBeNull()
  })
})

describe('applyOlderPage (prepend-merge)', () => {
  it('cannot clobber a completed tool\'s input with the {} of its first record', async () => {
    const { store } = await setup()
    const full = { file_path: '/a', content: 'x' }
    const older = [user(), assistant(), toolRecord('t1', 'pending', {})]
    const newer = [toolRecord('t1', 'pending', full), toolRecord('t1', 'completed', full, { result: 'ok' }), user()]
    serve([...older, ...newer], { tailStart: older.length, pageSize: 100 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    const page = await store.fetchOlderPage(SID, { signal: signalOf() })
    store.applyFetchedOlderPage(SID, page)
    const tool = store.toolCallsBySession.get(SID).find(t => t.id === 't1')
    expect(tool.input).toEqual(full)
    expect(tool.status).toBe('completed')
    expect(store.toolCallsBySession.get(SID)).toHaveLength(1)
  })

  it('keeps latest-wins side effects: stop reason, launch timestamp, deferred tool, init data', async () => {
    const { store, sessionStore } = await setup()
    const olderRecords = [
      msg('system', { subtype: 'client_launched', timestamp: 100, metadata: { subtype: 'client_launched' } }),
      msg('system', { subtype: 'init', metadata: { subtype: 'init', init_data: { model: 'OLD' } } }),
      user(),
      msg('result', { metadata: { stop_reason: 'max_tokens' } }),
    ]
    const newerRecords = [
      msg('system', { subtype: 'client_launched', timestamp: 900, metadata: { subtype: 'client_launched' } }),
      user(),
      msg('result', { metadata: { stop_reason: 'end_turn' } }),
    ]
    serve([...olderRecords, ...newerRecords], { tailStart: olderRecords.length, latestInit: { model: 'NEW' }, pageSize: 100 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    store.applyFetchedOlderPage(SID, await store.fetchOlderPage(SID, { signal: signalOf() }))
    expect(store.lastStopReasonBySession.get(SID)).toBe('end_turn')
    expect(store.launchTimestampBySession.get(SID)).toBe(900)
    expect(sessionStore.initData.get(SID)).toMatchObject({ model: 'NEW' })
  })

  it('prepends subagent narration in front of narration already held', async () => {
    const { store } = await setup()
    const narr = (text, ts) => ({
      type: 'assistant', message_id: `n-${text}`, timestamp: ts, content: text,
      metadata: { parent_tool_use_id: 'launch-1', turn_id: `nt-${text}` },
    })
    store.applyTaskLifecycleFrame(SID, 'task_started', { task_id: 'task-1', tool_use_id: 'launch-1', description: 'd' }, 1)
    store.applyOlderPage(SID, [narr('newer', 50)])
    store.applyOlderPage(SID, [narr('older-a', 10), narr('older-b', 20)])
    expect(store.narrationForLeg('task-1', 0).map(m => m.content)).toEqual(['older-a', 'older-b', 'newer'])
  })
})

describe('gating flag (turn navigation / PreCompact / search all read it)', () => {
  it('flips exactly when the last older page is applied', async () => {
    const { store } = await setup()
    serve(Array.from({ length: 6 }, () => user()), { tailStart: 4, pageSize: 1 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    const flags = []
    for (let i = 0; i < 4; i++) {
      flags.push(store.isHistoryComplete(SID))
      store.applyFetchedOlderPage(SID, await store.fetchOlderPage(SID, { signal: signalOf() }))
    }
    flags.push(store.isHistoryComplete(SID))
    expect(flags).toEqual([false, false, false, false, true])
  })

  it('PreCompact hooks resolve under the loaded compaction group key on a tail-only window (issue #2030 stage 2)', async () => {
    const { store } = await setup()
    const compacting = (id) => ({ type: 'system', message_id: id, timestamp: 5000, content: '', metadata: { subtype: 'status', init_data: { status: 'compacting' } } })
    const hook = (id, phase) => ({
      type: 'system', message_id: `${phase}-${id}`, timestamp: 5001,
      content: '', metadata: { subtype: phase === 'hs' ? 'hook_started' : 'hook_response', init_data: { hook_id: id, hook_event: 'PreCompact', hook_name: 'h', exit_code: 0 } },
    })
    const records = [user(), compacting('cs-old'), hook('h0', 'hs'), hook('h0', 'hr'), user(), compacting('cs-new'), hook('h1', 'hs'), hook('h1', 'hr'), user()]
    serve(records, { tailStart: 4 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    expect(store.isHistoryComplete(SID)).toBe(false)
    expect(store.hooksForCompaction(SID, 'cs-new')).toHaveLength(1)
    expect(store.hooksForCompaction(SID, 'cs-new')[0].hook_event).toBe('PreCompact')
    expect(store.hooksForCompaction(SID, 'cs-old')).toEqual([]) // not loaded yet
    expect(store.hooksForCompaction(SID, null)).toEqual([])
    store.applyFetchedOlderPage(SID, await store.fetchOlderPage(SID, { signal: signalOf() }))
    expect(store.hooksForCompaction(SID, 'cs-old')).toHaveLength(1)
    expect(store.hooksForCompaction(SID, 'cs-new')).toHaveLength(1)
  })
})

describe('history index store (issue #2030 stage 2 PR B)', () => {
  const turnsOf = (records) => records.filter(r => r.type === 'user').map(r => r.message_id)
  const indexCalls = () => apiMock.get.mock.calls.filter(([u]) => String(u).includes('/history_index'))

  async function loaded(options = {}) {
    const { store } = await setup()
    const records = Array.from({ length: 12 }, (_, i) => (i % 3 === 0 ? user() : assistant()))
    const server = serve(records, { tailStart: 6, pageSize: 3, index: { turns: turnsOf(records) }, ...options })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    return { store, records, server }
  }

  it('is not requested by loading the window (the scheduler asks after the snapshots)', async () => {
    const { store } = await loaded()
    expect(indexCalls()).toHaveLength(0)
    expect(store.getHistoryIndex(SID)).toBeNull()
  })

  it('fetches once from <baseUrl dir>/history_index at low priority and exposes turns/compactions', async () => {
    const { store, records } = await loaded()
    const [first, second] = [store.ensureHistoryIndex(SID), store.ensureHistoryIndex(SID)]
    expect(store.getHistoryIndex(SID).status).toBe('loading')
    const entry = await first
    await second
    expect(indexCalls()).toHaveLength(1)
    expect(indexCalls()[0]).toEqual([`/api/sessions/${SID}/history_index`, { signal: expect.anything(), priority: 'low' }])
    expect(entry).toMatchObject({ status: 'ready', historyId: 'h1' })
    expect(entry.turns.map(t => t[1])).toEqual(turnsOf(records))
    expect(store.getHistoryIndex(SID).status).toBe('ready')
    await store.ensureHistoryIndex(SID) // idempotent once ready
    expect(indexCalls()).toHaveLength(1)
  })

  it('uses the archive endpoint next to the archive messages URL', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 6 }, () => user())
    serve(records, { tailStart: 3, index: { turns: turnsOf(records) } })
    const baseUrl = '/api/projects/p1/archives/s1/a1/messages'
    await store.loadMessages(SID, null, 0, { window: 'tail', source: 'archive', baseUrl, signal: signalOf() })
    await store.ensureHistoryIndex(SID)
    expect(indexCalls()[0][0]).toBe('/api/projects/p1/archives/s1/a1/history_index')
  })

  it('does nothing once the whole history is loaded', async () => {
    const { store } = await setup()
    serve(Array.from({ length: 3 }, () => user()), { tailStart: 0 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    expect(await store.ensureHistoryIndex(SID)).toBeNull()
    expect(indexCalls()).toHaveLength(0)
  })

  it('an old server (404) leaves the index unavailable without retrying', async () => {
    const { store } = await loaded({ index: null })
    const entry = await store.ensureHistoryIndex(SID)
    expect(entry.status).toBe('unavailable')
    expect(indexCalls()).toHaveLength(1)
    await store.ensureHistoryIndex(SID)
    expect(indexCalls()).toHaveLength(1)
  })

  it('a 503 (index building) is retried once after a backoff, then unavailable', async () => {
    vi.useFakeTimers()
    try {
      const { store } = await loaded({ index: { turns: [] }, indexError: { status: 503, message: 'index_building' } })
      const pending = store.ensureHistoryIndex(SID)
      await vi.advanceTimersByTimeAsync(100)
      expect(indexCalls()).toHaveLength(1)
      await vi.advanceTimersByTimeAsync(6000)
      const entry = await pending
      expect(indexCalls()).toHaveLength(2)
      expect(entry.status).toBe('unavailable')
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops a response whose history_id no longer matches the loaded history', async () => {
    const { store, records } = await loaded()
    apiMock.get.mockImplementation(async (url) => (String(url).includes('/history_index')
      ? { history_id: 'other', total_bytes: 1, turns: [], compactions: [] }
      : createFakeHistoryServer(records, { tailStart: 6 }).handle(url)))
    expect(await store.ensureHistoryIndex(SID)).toBeNull()
    expect(store.getHistoryIndex(SID)).toBeNull()
  })

  it('dropping the index during the retry wait settles the awaited promise (no hung callers)', async () => {
    vi.useFakeTimers()
    try {
      const { store } = await loaded({ index: { turns: [] }, indexError: { status: 503, message: 'index_building' } })
      const pending = store.ensureHistoryIndex(SID)
      await vi.advanceTimersByTimeAsync(100) // first attempt failed, now waiting to retry
      store.clearMessages(SID)
      await expect(pending).resolves.toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('a transient failure is retried later (cooldown), a permanent one (404) never is', async () => {
    vi.useFakeTimers()
    try {
      const { store, records } = await loaded({ index: { turns: [] }, indexError: { status: 503, message: 'index_building' } })
      const pending = store.ensureHistoryIndex(SID)
      await vi.advanceTimersByTimeAsync(6000)
      expect((await pending).status).toBe('unavailable')
      // the server recovers; the cooldown retry picks it up without any user action
      serve(records, { tailStart: 6, pageSize: 3, index: { turns: turnsOf(records) } })
      await vi.advanceTimersByTimeAsync(31000)
      expect(store.getHistoryIndex(SID).status).toBe('ready')
    } finally {
      vi.useRealTimers()
    }
  })

  it('an index is unusable (unavailable) when the loaded start is not a b:<offset> cursor', async () => {
    const { store, records } = await loaded()
    apiMock.get.mockImplementation(async (url) => (String(url).includes('/history_index')
      ? { history_id: 'h1', total_bytes: 1, turns: [['x', 'u', 1]], compactions: [] }
      : createFakeHistoryServer(records, { tailStart: 6 }).handle(url)))
    store.getHistoryState(SID).startCursor = 'opaque'
    expect((await store.ensureHistoryIndex(SID)).status).toBe('unavailable')
  })

  it('is dropped when the suffix is replaced (gap-replace / restart) and refetched on demand', async () => {
    const { store, records } = await loaded()
    await store.ensureHistoryIndex(SID)
    expect(store.getHistoryIndex(SID).status).toBe('ready')
    serve(records, { tailStart: 8, index: { turns: turnsOf(records) } })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() }) // replaces
    expect(store.getHistoryIndex(SID)).toBeNull()
    await store.ensureHistoryIndex(SID)
    expect(store.getHistoryIndex(SID).status).toBe('ready')
  })

  it('survives a resync merge that keeps the loaded suffix', async () => {
    const { store, records } = await loaded()
    await store.ensureHistoryIndex(SID)
    serve(records, { tailStart: 7, index: { turns: turnsOf(records) } })
    const result = await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: signalOf() })
    expect(result.replaced).toBe(false)
    expect(store.getHistoryIndex(SID).status).toBe('ready')
  })

  it('is dropped (and an in-flight request aborted) when the session state is cleared', async () => {
    const { store } = await loaded()
    let aborted = false
    apiMock.get.mockImplementation((url, opts) => (String(url).includes('/history_index')
      ? new Promise((_res, rej) => opts.signal.addEventListener('abort', () => { aborted = true; rej(new DOMException('Aborted', 'AbortError')) }))
      : Promise.reject(new Error('unexpected'))))
    const pending = store.ensureHistoryIndex(SID)
    store.clearMessages(SID)
    expect(await pending).toBeNull()
    expect(aborted).toBe(true)
    expect(store.getHistoryIndex(SID)).toBeNull()
  })

  it('isMessageLoaded reflects the loaded suffix and grows with older pages', async () => {
    const { store, records } = await loaded()
    expect(store.isMessageLoaded(SID, records[7].message_id)).toBe(true)
    expect(store.isMessageLoaded(SID, records[0].message_id)).toBe(false)
    store.applyFetchedOlderPage(SID, await store.fetchOlderPage(SID, { signal: signalOf() }))
    expect(store.isMessageLoaded(SID, records[4].message_id)).toBe(true)
  })
})

describe('resync (stream_reset / evicted)', () => {
  it('merges the fresh window onto the loaded suffix when they overlap and keeps the older part', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 12 }, (_, i) => (i % 2 ? assistant() : user()))
    serve(records, { tailStart: 8, pageSize: 4 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    store.applyFetchedOlderPage(SID, await store.fetchOlderPage(SID, { signal: signalOf() }))
    expect(store.messagesBySession.get(SID)).toHaveLength(8)

    const grown = [...records, user(), assistant()]
    const server = serve(grown, { tailStart: 10 })
    const result = await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: signalOf() })
    expect(server.requests).toHaveLength(1)
    expect(result.replaced).toBe(false)
    expect(store.messagesBySession.get(SID)).toHaveLength(10)
    expect(store.getHistoryState(SID).startCursor).toBe('b:4') // older part retained, no re-backfill
  })

  it('replaces the state and restarts backfill when the gap is larger than the window', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 12 }, () => user())
    serve(records, { tailStart: 8 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    const generation = store.getHistoryState(SID).generation

    const grown = [...records, ...Array.from({ length: 30 }, () => user())]
    serve(grown, { tailStart: 32 })
    const result = await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: signalOf() })
    expect(result.replaced).toBe(true)
    expect(store.messagesBySession.get(SID)).toHaveLength(10)
    expect(store.getHistoryState(SID)).toMatchObject({ startCursor: 'b:32', hasMoreBefore: true })
    expect(store.getHistoryState(SID).generation).toBeGreaterThan(generation)
  })

  it('replaces the state when history_id changed (file rewritten)', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 12 }, () => user())
    serve(records, { tailStart: 8 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    serve(records, { tailStart: 8, historyId: 'rewritten' })
    const result = await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: signalOf() })
    expect(result.replaced).toBe(true)
    expect(store.getHistoryState(SID).historyId).toBe('rewritten')
  })

  it('live events overlapping the window tail are deduplicated by message_id', async () => {
    const { store } = await setup()
    const records = Array.from({ length: 6 }, () => user())
    serve(records, { tailStart: 3 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    store.applyRecord(SID, records[5], 'live') // already in the window
    store.applyRecord(SID, user(), 'live') // genuinely new
    expect(store.messagesBySession.get(SID)).toHaveLength(4)
  })
})

describe('clearing', () => {
  it('clearMessages drops the history state and aborts an in-flight backfill page', async () => {
    const { store } = await setup()
    serve(Array.from({ length: 6 }, () => user()), { tailStart: 3 })
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: signalOf() })
    const controller = store.claimBackfill(SID)
    store.clearMessages(SID)
    expect(controller.signal.aborted).toBe(true)
    expect(store.getHistoryState(SID)).toBeNull()
    expect(store.isHistoryComplete(SID)).toBe(true)
  })

  it('claiming a new backfill request aborts the previous one', async () => {
    const { store } = await setup()
    const first = store.claimBackfill(SID)
    store.claimBackfill(SID)
    expect(first.signal.aborted).toBe(true)
  })
})

describe('reactions to backfilled history (issue #2030)', () => {
  it('a tool that arrives with an older page does not trigger an edit-history refresh, a live completion does', async () => {
    vi.useFakeTimers()
    try {
      const { store } = await setup()
      const { useEditHistoryStore } = await import('@/stores/editHistory')
      const editHistory = useEditHistoryStore()
      editHistory.entriesBySession.set(SID, { entries: [], tool_count: 0, loading: false, error: null })

      apiMock.get.mockImplementation((url) => (String(url).includes('/edit-history') ? Promise.resolve({ entries: [], tool_count: 0 }) : Promise.resolve({})))
      const completedWrite = (id) => toolRecord(id, 'completed', { file_path: '/a' }, { result: 'ok' })
      store.applyOlderPage(SID, [user(), completedWrite('old-1')])
      await vi.advanceTimersByTimeAsync(1000)
      expect(apiMock.get.mock.calls.filter(([u]) => String(u).includes('/edit-history'))).toHaveLength(0)
      expect(store.wasBackfilled(SID, 'old-1')).toBe(true)

      store.applyRecord(SID, completedWrite('live-1'), 'live')
      await vi.advanceTimersByTimeAsync(1000)
      expect(apiMock.get.mock.calls.filter(([u]) => String(u).includes('/edit-history'))).toHaveLength(1)
      expect(store.wasBackfilled(SID, 'live-1')).toBe(false)

      // A tool that was still running in the older page and completes live is news, not history.
      store.applyOlderPage(SID, [toolRecord('straddle', 'pending', {})])
      expect(store.wasBackfilled(SID, 'straddle')).toBe(true)
      store.applyRecord(SID, completedWrite('straddle'), 'live')
      expect(store.wasBackfilled(SID, 'straddle')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
