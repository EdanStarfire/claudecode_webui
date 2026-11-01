import { describe, it, expect, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { useMessageSearch } from '@/composables/useMessageSearch'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

function makeFakeStore(messagesBySessionEntries = []) {
  return {
    messagesBySession: new Map(messagesBySessionEntries),
    toolCallsBySession: new Map(),
    toolIndexBySession: new Map(),
  }
}

describe('useMessageSearch', () => {
  it('debounces query input before running the search', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [{ id: 'm1', content: 'find the needle', metadata: {} }]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.query.value = 'needle'
    expect(search.matches.value).toEqual([])

    await vi.advanceTimersByTimeAsync(199)
    expect(search.matches.value).toEqual([])

    await vi.advanceTimersByTimeAsync(1)
    expect(search.matches.value).toHaveLength(1)
    expect(search.currentIndex.value).toBe(0)
  })

  it('next() wraps from the last match back to the first', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [
        { id: 'm1', content: 'needle one', metadata: {} },
        { id: 'm2', content: 'needle two', metadata: {} },
      ]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)
    expect(search.matches.value).toHaveLength(2)

    expect(search.currentIndex.value).toBe(0)
    search.next()
    expect(search.currentIndex.value).toBe(1)
    search.next()
    expect(search.currentIndex.value).toBe(0)
  })

  it('prev() wraps from the first match to the last', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [
        { id: 'm1', content: 'needle one', metadata: {} },
        { id: 'm2', content: 'needle two', metadata: {} },
      ]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)

    expect(search.currentIndex.value).toBe(0)
    search.prev()
    expect(search.currentIndex.value).toBe(1)
  })

  it('next()/prev() are no-ops when there are no matches', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([['s1', []]])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)
    expect(search.matches.value).toEqual([])

    search.next()
    search.prev()
    expect(search.currentIndex.value).toBe(-1)
  })

  it('resets query/matches/currentIndex when the session id changes', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [{ id: 'm1', content: 'needle', metadata: {} }]],
      ['s2', [{ id: 'm2', content: 'no match here', metadata: {} }]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)
    expect(search.matches.value).toHaveLength(1)

    sessionId.value = 's2'
    await nextTick()
    expect(search.query.value).toBe('')
    expect(search.matches.value).toEqual([])
    expect(search.currentIndex.value).toBe(-1)
  })

  it('resets state on close()', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [{ id: 'm1', content: 'needle', metadata: {} }]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.open()
    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)
    expect(search.matches.value).toHaveLength(1)

    search.close()
    expect(search.isOpen.value).toBe(false)
    expect(search.query.value).toBe('')
    expect(search.matches.value).toEqual([])
    expect(search.currentIndex.value).toBe(-1)
  })

  it('markNotFound sets notFound, cleared by the next search run or navigation', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [
        { id: 'm1', content: 'needle one', metadata: {} },
        { id: 'm2', content: 'needle two', metadata: {} },
      ]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)

    search.markNotFound()
    expect(search.notFound.value).toBe(true)

    search.next()
    expect(search.notFound.value).toBe(false)
  })

  it('currentMatch is null when there are no matches and reflects currentIndex otherwise', async () => {
    vi.useFakeTimers()
    const messageStore = makeFakeStore([
      ['s1', [{ id: 'm1', content: 'needle', metadata: {} }]],
    ])
    const sessionId = ref('s1')
    const search = useMessageSearch(sessionId, { messageStore })

    expect(search.currentMatch.value).toBeNull()

    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)
    expect(search.currentMatch.value).toMatchObject({ messageId: 'm1' })
  })
})

describe('useMessageSearch — partially loaded history (issue #2030)', () => {
  async function realStore() {
    const { setActivePinia, createPinia } = await import('pinia')
    setActivePinia(createPinia())
    const { useMessageStore } = await import('@/stores/message')
    return useMessageStore()
  }

  const record = (id, content) => ({ type: 'assistant', message_id: id, content, timestamp: 1, metadata: { turn_id: `t-${id}` } })

  // A partially loaded session (newest 4 of 8 records) backed by the fake history server, plus a
  // `/search` handler the test scripts through `onSearch(url, params)`.
  async function partialSession(onSearch) {
    const store = await realStore()
    const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
    const records = Array.from({ length: 8 }, (_, i) => record(`m${i}`, `needle ${i}`))
    const server = createFakeHistoryServer(records, { tailStart: 4, pageSize: 4 })
    apiMock.get.mockReset()
    apiMock.get.mockImplementation((url, options) => {
      if (url.includes('/search?')) return onSearch(url, new URLSearchParams(url.split('?')[1]), options)
      return server.handle(url)
    })
    await store.loadMessages('s1', null, 0, { window: 'tail', signal: new AbortController().signal })
    return { store, server }
  }

  const hit = (id, extra = {}) => ({
    message_id: id, tool_id: null, field: 'content', match_start: 0, match_length: 6,
    snippet: `needle ${id}`, occurrence_index: 0, cursor: `b:${id.slice(1) * 100}`, size_hint: id.slice(1) * 100, ...extra,
  })
  const page = (ids, extra = {}) => ({
    results: ids.map(id => hit(id)), next_cursor: 'b:1', exhausted: false, scanned_fraction: 0.5, history_id: 'h', ...extra,
  })
  const searchCalls = () => apiMock.get.mock.calls.filter(([url]) => url.includes('/search?'))

  async function typeQuery(search, text = 'needle') {
    search.query.value = text
    await vi.advanceTimersByTimeAsync(200)
  }

  it('searches the whole history on the server: newest-first pages, N+ until exhausted, client_id on every request', async () => {
    vi.useFakeTimers()
    const pages = [page(['m7', 'm6']), page(['m5'], { next_cursor: null, exhausted: true })]
    const { store } = await partialSession(() => pages.shift())
    const search = useMessageSearch(ref('s1'), { messageStore: store })

    await typeQuery(search)
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7', 'm6'])
    expect(search.hasMore.value).toBe(true)
    expect(search.phase.value).toBe('server')
    expect(search.matches.value[0]).toMatchObject({ toolId: null, field: 'content', cursor: 'b:700', sizeHint: 700, occurrenceIndex: 0 })
    expect(search.loadedOnly.value).toBe(false)

    await search.loadMore()
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7', 'm6', 'm5'])
    expect(search.hasMore.value).toBe(false)

    const calls = searchCalls().map(([url]) => new URLSearchParams(url.split('?')[1]))
    expect(calls).toHaveLength(2)
    expect(calls[0].get('cursor')).toBeNull()
    expect(calls[1].get('cursor')).toBe('b:1')
    expect(calls[0].get('client_id')).toBeTruthy()
    expect(calls[1].get('client_id')).toBe(calls[0].get('client_id'))
    expect(calls[0].get('q')).toBe('needle')
    expect(searchCalls()[0][0]).toMatch(/^\/api\/sessions\/s1\/search\?/)
  })

  it('uses the archive search route when the history was loaded from an archive', async () => {
    vi.useFakeTimers()
    const { store } = await partialSession(() => page(['m7'], { next_cursor: null, exhausted: true }))
    store.getHistoryState('s1').baseUrl = '/api/projects/p1/archives/s1/a1/messages'
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(searchCalls()[0][0]).toMatch(/^\/api\/projects\/p1\/archives\/s1\/a1\/search\?/)
  })

  it('next() past the last loaded result fetches the next page and steps into it', async () => {
    vi.useFakeTimers()
    const pages = [page(['m7']), page(['m6', 'm5'], { next_cursor: null, exhausted: true })]
    const { store } = await partialSession(() => pages.shift())
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(search.matches.value).toHaveLength(1)

    await search.next()
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7', 'm6', 'm5'])
    expect(search.currentIndex.value).toBe(1)
    // Exhausted: wraps like the loaded search.
    await search.next()
    await search.next()
    expect(search.currentIndex.value).toBe(0)
  })

  it('keeps fetching while a page comes back empty but the scan is not finished', async () => {
    vi.useFakeTimers()
    const pages = [
      { results: [], next_cursor: 'b:9', exhausted: false, scanned_fraction: 0.3 },
      { results: [], next_cursor: 'b:5', exhausted: false, scanned_fraction: 0.6 },
      page(['m2'], { next_cursor: null, exhausted: true }),
    ]
    const { store } = await partialSession(() => pages.shift())
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(searchCalls()).toHaveLength(3)
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m2'])
  })

  it('reports "No results" for an exhausted empty search', async () => {
    vi.useFakeTimers()
    const { store } = await partialSession(() => ({ results: [], next_cursor: null, exhausted: true }))
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(search.matches.value).toEqual([])
    expect(search.hasMore.value).toBe(false)
    expect(search.phase.value).toBe('server')
  })

  it('aborts the older request of a newer query and ignores its late (superseded) answer', async () => {
    vi.useFakeTimers()
    const deferred = []
    const { store } = await partialSession((url, params, options) => new Promise((resolve) => {
      deferred.push({ q: params.get('q'), resolve, signal: options.signal })
    }))
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search, 'needle')
    await typeQuery(search, 'needle 7')
    expect(deferred).toHaveLength(2)
    expect(deferred[0].signal.aborted).toBe(true)
    expect(deferred[1].signal.aborted).toBe(false)

    deferred[0].resolve({ results: [], next_cursor: null, exhausted: false, superseded: true })
    deferred[1].resolve(page(['m7'], { next_cursor: null, exhausted: true }))
    await vi.advanceTimersByTimeAsync(0)
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7'])
    expect(search.phase.value).toBe('server')
    expect(searchCalls()).toHaveLength(2) // no retry caused by the old query's superseded answer
  })

  it('retries once when the CURRENT query is answered superseded, then falls back to the loaded messages', async () => {
    vi.useFakeTimers()
    const superseded = { results: [], next_cursor: null, exhausted: false, superseded: true }
    const { store } = await partialSession(() => superseded)
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(searchCalls()).toHaveLength(2)
    expect(search.phase.value).toBe('fallback')
    expect(search.loadedOnly.value).toBe(true)
    expect(search.matches.value).toHaveLength(4) // loaded messages only
  })

  it.each([
    ['503 search_busy', { status: 503, message: 'search_busy' }],
    ['404 (old server)', { status: 404, message: 'Not Found' }],
    ['network failure', { status: 0, message: 'Network error: Failed to fetch' }],
    ['request timeout', { timedOut: true, message: 'Search timed out' }],
    ['400', { status: 400, message: 'query_too_short' }],
  ])('falls back to the loaded messages with the loaded-only indicator on %s', async (_name, failure) => {
    vi.useFakeTimers()
    const { store } = await partialSession(() => Promise.reject(Object.assign(new Error(failure.message), failure)))
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(search.phase.value).toBe('fallback')
    expect(search.loadedOnly.value).toBe(true)
    expect(search.matches.value).toHaveLength(4)
    expect(search.hasMore.value).toBe(false)

    // The fallback keeps the stage-1b behaviour: an older page re-runs the query, selection kept.
    await search.next()
    const selected = search.currentMatch.value.messageId
    store.applyFetchedOlderPage('s1', await store.fetchOlderPage('s1', { signal: new AbortController().signal }))
    await vi.advanceTimersByTimeAsync(300)
    expect(search.matches.value).toHaveLength(8)
    expect(search.currentMatch.value.messageId).toBe(selected)
    expect(search.loadedOnly.value).toBe(false) // everything is loaded now
  })

  it('restarts the search from the beginning on 409 cursor_invalid, and gives up after repeated 409s', async () => {
    vi.useFakeTimers()
    let call = 0
    const { store } = await partialSession((url, params) => {
      call++
      if (call === 2) return Promise.reject(Object.assign(new Error('cursor_invalid'), { status: 409 }))
      if (call === 1) return page(['m7'])
      return page(['m6'], { next_cursor: null, exhausted: true })
    })
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    await search.loadMore() // call 2 -> 409 -> restart (call 3, cursor null)
    expect(search.phase.value).toBe('server')
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m6'])
    const cursors = searchCalls().map(([url]) => new URLSearchParams(url.split('?')[1]).get('cursor'))
    expect(cursors).toEqual([null, 'b:1', null])

    const { store: store2 } = await partialSession(() => Promise.reject(Object.assign(new Error('cursor_invalid'), { status: 409 })))
    const search2 = useMessageSearch(ref('s1'), { messageStore: store2 })
    await typeQuery(search2)
    expect(search2.phase.value).toBe('fallback')
  })

  it('does not call the server when the whole history is loaded', async () => {
    vi.useFakeTimers()
    const { store } = await partialSession(() => { throw new Error('unexpected search request') })
    store.applyFetchedOlderPage('s1', await store.fetchOlderPage('s1', { signal: new AbortController().signal }))
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(searchCalls()).toHaveLength(0)
    expect(search.phase.value).toBe('local')
    expect(search.matches.value).toHaveLength(8)
    expect(search.loadedOnly.value).toBe(false)
  })

  it('uses the smaller page limit it is given, and nothing is requested before the user types', async () => {
    vi.useFakeTimers()
    const { store } = await partialSession(() => page(['m7'], { next_cursor: null, exhausted: true }))
    const search = useMessageSearch(ref('s1'), { messageStore: store, getPageLimit: () => 20 })
    await vi.advanceTimersByTimeAsync(5000)
    expect(searchCalls()).toHaveLength(0)
    await typeQuery(search)
    expect(new URLSearchParams(searchCalls()[0][0].split('?')[1]).get('limit')).toBe('20')
  })

  it('closing the search aborts the in-flight request', async () => {
    vi.useFakeTimers()
    let signal
    const { store } = await partialSession((url, params, options) => { signal = options.signal; return new Promise(() => {}) })
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(search.phase.value).toBe('searching')
    search.close()
    expect(signal.aborted).toBe(true)
    expect(search.phase.value).toBe('idle')
  })

  it('a 409 while loading more restarts counting from zero (stops after the first new page)', async () => {
    vi.useFakeTimers()
    let call = 0
    const { store } = await partialSession(() => {
      call++
      if (call === 1) return page(['m7', 'm6', 'm5'])
      if (call === 2) return Promise.reject(Object.assign(new Error('cursor_invalid'), { status: 409 }))
      if (call === 3) return page(['m7'])
      return page(['m4'], { next_cursor: null, exhausted: true })
    })
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    await search.loadMore()
    expect(searchCalls()).toHaveLength(3) // restart page 1 already yields a new match: no further paging
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7'])
  })

  it('keeps the shown results when a later page fails (the button retries) instead of falling back', async () => {
    vi.useFakeTimers()
    let call = 0
    const { store } = await partialSession(() => {
      call++
      if (call === 1) return page(['m7'])
      if (call === 2) return Promise.reject(Object.assign(new Error('search_busy'), { status: 503 }))
      return page(['m6'], { next_cursor: null, exhausted: true })
    })
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    await search.loadMore()
    expect(search.phase.value).toBe('server')
    expect(search.loadedOnly.value).toBe(false)
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7'])
    expect(search.hasMore.value).toBe(true)
    await search.loadMore()
    expect(search.matches.value.map(m => m.messageId)).toEqual(['m7', 'm6'])
  })

  it('a failing server is not retried on every older page applied during the fallback', async () => {
    vi.useFakeTimers()
    const { store } = await partialSession(() => Promise.reject(Object.assign(new Error('x'), { status: 503 })))
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    expect(searchCalls()).toHaveLength(1)
    // one more older page (4 -> 8 records) keeps the fallback local; no new /search request
    store.applyFetchedOlderPage('s1', await store.fetchOlderPage('s1', { signal: new AbortController().signal }))
    await vi.advanceTimersByTimeAsync(300)
    expect(searchCalls()).toHaveLength(1)
    expect(search.matches.value).toHaveLength(8)
  })

  it('next() ignores presses while a page loads, wraps when the last page adds nothing, and navTick moves on every press', async () => {
    vi.useFakeTimers()
    let release
    let call = 0
    const { store } = await partialSession(() => {
      call++
      if (call === 1) return page(['m7'])
      return new Promise(resolve => { release = () => resolve({ results: [], next_cursor: null, exhausted: true }) })
    })
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    const tick0 = search.navTick.value
    const first = search.next()
    await search.next() // loading: ignored
    expect(searchCalls()).toHaveLength(2)
    release()
    await first
    expect(search.currentIndex.value).toBe(0) // exhausted without new results: wrapped
    expect(search.navTick.value).toBe(tick0 + 1)
    await search.next()
    expect(search.navTick.value).toBe(tick0 + 2) // single result: still re-navigates
  })

  it('keeps occurrence indexes and selection stable when more pages are appended', async () => {
    vi.useFakeTimers()
    const pages = [
      { ...page([]), results: [hit('m7', { occurrence_index: 0, match_start: 0 }), hit('m7', { occurrence_index: 1, match_start: 9 })] },
      page(['m6'], { next_cursor: null, exhausted: true }),
    ]
    const { store } = await partialSession(() => pages.shift())
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await typeQuery(search)
    await search.next()
    expect(search.currentMatch.value).toMatchObject({ messageId: 'm7', occurrenceIndex: 1 })
    await search.loadMore()
    expect(search.currentMatch.value).toMatchObject({ messageId: 'm7', occurrenceIndex: 1 })
    expect(search.matches.value.map(m => `${m.messageId}:${m.occurrenceIndex}`)).toEqual(['m7:0', 'm7:1', 'm6:0'])
  })
})

describe('useMessageSearch — result order and local/server transitions (issue #2030 stage 3)', () => {
  // A store whose completeness the test flips; `needle` messages m1..m3, m2 has two matches.
  function flexStore() {
    const state = ref(false) // history complete?
    const cursor = ref('b:100')
    const messages = [
      { id: 'm1', content: 'needle one', metadata: {} },
      { id: 'm2', content: 'needle two needle again', metadata: {} },
      { id: 'm3', content: 'needle three', metadata: {} },
    ]
    return {
      state,
      cursor,
      messagesBySession: new Map([['s1', messages]]),
      toolCallsBySession: new Map(),
      toolIndexBySession: new Map(),
      isHistoryComplete: () => state.value,
      getHistoryState: () => ({ startCursor: cursor.value, baseUrl: '/api/sessions/s1/messages' }),
    }
  }
  const serverPage = (ids) => ({
    results: ids.map(id => ({ message_id: id, tool_id: null, field: 'content', match_start: 0, match_length: 6, snippet: 's', occurrence_index: 0, cursor: 'b:1', size_hint: 1 })),
    next_cursor: null, exhausted: true, history_id: 'h',
  })
  const type = async (search, text) => { search.query.value = text; await vi.advanceTimersByTimeAsync(200) }

  it('local results use the server convention: newest message first, a message\'s own matches in occurrence order', async () => {
    vi.useFakeTimers()
    const store = flexStore()
    store.state.value = true
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await type(search, 'needle')
    expect(search.matches.value.map(m => `${m.messageId}:${m.occurrenceIndex}`)).toEqual(['m3:0', 'm2:0', 'm2:1', 'm1:0'])
    expect(search.currentMatch.value.messageId).toBe('m3') // result 1 == newest
    await search.next()
    expect(search.currentMatch.value.messageId).toBe('m2') // next == older
    search.prev()
    expect(search.currentMatch.value.messageId).toBe('m3') // prev == newer
  })

  it('a complete history that a memory trim made partial again is searched on the server (issue #2030 stage 4 PR 4d)', async () => {
    vi.useFakeTimers()
    apiMock.get.mockReset()
    apiMock.get.mockResolvedValue(serverPage(['m3', 'm2', 'm1']))
    const store = flexStore()
    store.state.value = true
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await type(search, 'needle')
    expect(search.phase.value).toBe('local')
    expect(apiMock.get).not.toHaveBeenCalled()
    store.state.value = false // a trim released the old end...
    store.cursor.value = 'b:200' // ...which moves the loaded start
    await vi.advanceTimersByTimeAsync(1000)
    expect(search.phase.value).toBe('server')
    expect(apiMock.get).toHaveBeenCalledTimes(1)
  })

  it('a server search keeps its results, selection and highlight key when the history completes meanwhile', async () => {
    vi.useFakeTimers()
    apiMock.get.mockReset()
    apiMock.get.mockResolvedValue(serverPage(['m3', 'm2', 'm1']))
    const store = flexStore()
    const search = useMessageSearch(ref('s1'), { messageStore: store })
    await type(search, 'needle')
    await search.next()
    const before = { ids: search.matches.value.map(m => m.messageId), index: search.currentIndex.value, key: search.currentMatch.value }
    store.state.value = true // history completes mid-navigation
    await vi.advanceTimersByTimeAsync(1000)
    expect(search.matches.value.map(m => m.messageId)).toEqual(before.ids)
    expect(search.currentIndex.value).toBe(before.index)
    expect(search.currentMatch.value).toBe(before.key)
    expect(search.phase.value).toBe('server')
    expect(apiMock.get).toHaveBeenCalledTimes(1)

    // the next query uses the local search (no request)...
    await type(search, 'needle t')
    expect(search.phase.value).toBe('local')
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    // ...and once the history is incomplete again (reset / resync gap-replace) the next query uses the server
    store.state.value = false
    await type(search, 'needle th')
    expect(search.phase.value).toBe('server')
    expect(apiMock.get).toHaveBeenCalledTimes(2)
  })
})
