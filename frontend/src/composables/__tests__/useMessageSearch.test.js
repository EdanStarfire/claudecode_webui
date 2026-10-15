import { describe, it, expect, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { useMessageSearch } from '@/composables/useMessageSearch'

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
