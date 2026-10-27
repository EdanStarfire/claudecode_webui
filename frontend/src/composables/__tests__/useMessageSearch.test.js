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

  it('exposes the loaded-history scope while older history is loading, and null once complete', async () => {
    const store = await realStore()
    const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
    const records = Array.from({ length: 8 }, (_, i) => record(`m${i}`, `needle ${i}`))
    const server = createFakeHistoryServer(records, { tailStart: 4, pageSize: 4 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await store.loadMessages('s1', null, 0, { window: 'tail', signal: new AbortController().signal })

    const search = useMessageSearch(ref('s1'), { messageStore: store })
    expect(search.historyScope.value).toMatchObject({ percent: 50 })

    vi.useFakeTimers()
    search.query.value = 'needle'
    await vi.advanceTimersByTimeAsync(200)
    expect(search.matches.value).toHaveLength(4)
    search.next()
    const selected = search.currentMatch.value.messageId

    // An older page lands: the active query re-runs, new matches appear, the selection is kept.
    store.applyFetchedOlderPage('s1', await store.fetchOlderPage('s1', { signal: new AbortController().signal }))
    await vi.advanceTimersByTimeAsync(300)
    expect(search.matches.value).toHaveLength(8)
    expect(search.currentMatch.value.messageId).toBe(selected)
    expect(search.historyScope.value).toBeNull()
  })
})
