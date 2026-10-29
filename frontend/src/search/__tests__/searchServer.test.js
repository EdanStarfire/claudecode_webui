import { describe, it, expect, vi, afterEach } from 'vitest'
import { searchServer, getSearchClientId, toMatchDescriptor, SEARCH_REQUEST_TIMEOUT_MS } from '@/search/messageSearch'

const apiMock = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock }))

afterEach(() => {
  apiMock.get.mockReset()
  vi.useRealTimers()
})

const row = {
  message_id: 'm1', tool_id: 'tu1', field: 'tool_input', match_start: 4, match_length: 3, snippet: 'abc', occurrence_index: 2,
  cursor: 'b:120', size_hint: 120, type: 'tool_call', timestamp: 1,
}

describe('searchServer (issue #2030 stage 3)', () => {
  it('maps the server payload to the findMatches descriptor shape plus cursor/sizeHint', async () => {
    apiMock.get.mockResolvedValue({
      results: [row, { ...row, tool_id: null, field: 'content' }], next_cursor: 'b:5', exhausted: false, scanned_fraction: 0.25, history_id: 'h1',
    })
    const out = await searchServer('/api/sessions/s1/search', '  Needle ', null, {})
    expect(out).toEqual({
      superseded: false,
      matches: [
        { messageId: 'm1', toolId: 'tu1', field: 'tool_input', matchStart: 4, matchLength: 3, snippet: 'abc', occurrenceIndex: 2, cursor: 'b:120', sizeHint: 120 },
        { messageId: 'm1', toolId: null, field: 'content', matchStart: 4, matchLength: 3, snippet: 'abc', occurrenceIndex: 2, cursor: 'b:120', sizeHint: 120 },
      ],
      nextCursor: 'b:5', exhausted: false, scannedFraction: 0.25, historyId: 'h1',
    })
  })

  it('sends the trimmed query, the per-tab client_id on every call, the cursor and the limit', async () => {
    apiMock.get.mockResolvedValue({ results: [], next_cursor: null, exhausted: true })
    await searchServer('/api/sessions/s1/search', ' a&b ', null, { limit: 20 })
    await searchServer('/api/sessions/s1/search', ' a&b ', 'b:9', {})
    const [first, second] = apiMock.get.mock.calls.map(([url]) => new URLSearchParams(url.split('?')[1]))
    expect(first.get('q')).toBe('a&b')
    expect(first.get('limit')).toBe('20')
    expect(first.get('cursor')).toBeNull()
    expect(second.get('cursor')).toBe('b:9')
    expect(first.get('client_id')).toBe(getSearchClientId())
    expect(second.get('client_id')).toBe(getSearchClientId())
    expect(getSearchClientId()).toBeTruthy()
    expect(getSearchClientId().length).toBeLessThanOrEqual(64)
  })

  it('passes a superseded answer through and leaves API errors (status) untouched', async () => {
    apiMock.get.mockResolvedValueOnce({ results: [], next_cursor: null, exhausted: false, superseded: true })
    expect((await searchServer('/u', 'ab', null, {})).superseded).toBe(true)
    const apiError = Object.assign(new Error('search_busy'), { status: 503 })
    apiMock.get.mockRejectedValueOnce(apiError)
    await expect(searchServer('/u', 'ab', null, {})).rejects.toBe(apiError)
  })

  it('rejects with timedOut when the server never answers, but not when the caller aborted', async () => {
    vi.useFakeTimers()
    apiMock.get.mockImplementation((url, { signal }) => new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(Object.assign(new Error('Network error: aborted'), { status: 0 })))
    }))
    const timedOut = searchServer('/u', 'ab', null, {})
    const assertion = expect(timedOut).rejects.toMatchObject({ timedOut: true })
    await vi.advanceTimersByTimeAsync(SEARCH_REQUEST_TIMEOUT_MS + 1)
    await assertion

    const controller = new AbortController()
    const aborted = searchServer('/u', 'ab', null, { signal: controller.signal })
    const abortedAssertion = expect(aborted).rejects.not.toHaveProperty('timedOut')
    controller.abort()
    await abortedAssertion
  })

  it('toMatchDescriptor tolerates a missing tool_id / size_hint', () => {
    expect(toMatchDescriptor({ message_id: 'm', field: 'content', match_start: 0, match_length: 2, snippet: 's', occurrence_index: 0 }))
      .toMatchObject({ toolId: null, cursor: null, sizeHint: null })
  })
})
