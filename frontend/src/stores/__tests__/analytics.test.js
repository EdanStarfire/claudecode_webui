import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { computeFilteredTotals } from '@/stores/analytics'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
}))

vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn(() => null),
}))

function makeRow(overrides = {}) {
  return {
    session_id: 's1',
    session_name: 'session-one',
    model: 'claude-sonnet-4-6',
    input_tokens: 10,
    output_tokens: 20,
    cache_write_tokens: 0,
    cache_read_tokens: 0,
    estimated_cost_usd: 1,
    ...overrides,
  }
}

describe('computeFilteredTotals (issue #1833)', () => {
  it('sums token fields and cost across rows', () => {
    const rows = [
      makeRow({ session_id: 's1', input_tokens: 10, output_tokens: 20, cache_write_tokens: 1, cache_read_tokens: 2, estimated_cost_usd: 1 }),
      makeRow({ session_id: 's2', input_tokens: 5, output_tokens: 7, cache_write_tokens: 3, cache_read_tokens: 4, estimated_cost_usd: 2 }),
    ]
    const totals = computeFilteredTotals(rows)
    expect(totals.input_tokens).toBe(15)
    expect(totals.output_tokens).toBe(27)
    expect(totals.cache_write_tokens).toBe(4)
    expect(totals.cache_read_tokens).toBe(6)
    expect(totals.estimated_cost_usd).toBe(3)
    expect(totals.session_count).toBe(2)
  })

  it('picks the highest-cost row as top_session', () => {
    const rows = [
      makeRow({ session_id: 's1', session_name: 'cheap', estimated_cost_usd: 1 }),
      makeRow({ session_id: 's2', session_name: 'expensive', estimated_cost_usd: 5 }),
    ]
    const totals = computeFilteredTotals(rows)
    expect(totals.top_session).toEqual({ session_id: 's2', session_name: 'expensive', estimated_cost_usd: 5 })
  })

  it('returns null top_session and zeroed totals for an empty input', () => {
    const totals = computeFilteredTotals([])
    expect(totals.top_session).toBeNull()
    expect(totals.session_count).toBe(0)
    expect(totals.estimated_cost_usd).toBe(0)
    expect(totals.input_tokens).toBe(0)
  })
})

describe('analytics store — filter-scoped chart/summary fetch (issue #1833)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiMock.get.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function getStore() {
    const { useAnalyticsStore } = await import('@/stores/analytics')
    return useAnalyticsStore()
  }

  function respondForParams() {
    apiMock.get.mockImplementation((_url, { params } = {}) => {
      if (params.group_by === 'session') {
        return Promise.resolve({
          rows: [
            makeRow({ session_id: 's1', session_name: 'alpha', model: 'model-a' }),
            makeRow({ session_id: 's2', session_name: 'beta', model: 'model-b' }),
          ],
          totals: { session_count: 2 },
        })
      }
      return Promise.resolve({ buckets: [] })
    })
  }

  it('omits session_ids from the bucket request when no filter is active', async () => {
    respondForParams()
    const store = await getStore()
    await store.fetchData()

    const bucketCall = apiMock.get.mock.calls.find(([, opts]) => opts.params.group_by !== 'session')
    expect(bucketCall[1].params.session_ids).toBeUndefined()
  })

  it('includes session_ids on the bucket request once a filter narrows the rows', async () => {
    respondForParams()
    const store = await getStore()
    await store.fetchData()
    apiMock.get.mockClear()

    vi.useFakeTimers()
    store.setSessionSearch('alpha')
    await vi.advanceTimersByTimeAsync(250)

    const bucketCall = apiMock.get.mock.calls.find(([, opts]) => opts.params.group_by !== 'session')
    expect(bucketCall[1].params.session_ids).toBe('s1')
  })

  it('skips the network call and zeroes buckets when the filter matches nothing', async () => {
    respondForParams()
    const store = await getStore()
    await store.fetchData()
    apiMock.get.mockClear()

    vi.useFakeTimers()
    store.setSessionSearch('no-such-session')
    await vi.advanceTimersByTimeAsync(250)

    expect(apiMock.get).not.toHaveBeenCalled()
    expect(store.buckets).toEqual([])
  })

  it('restores fleet-wide data once the filter is cleared', async () => {
    respondForParams()
    const store = await getStore()
    await store.fetchData()

    vi.useFakeTimers()
    store.setSessionSearch('alpha')
    await vi.advanceTimersByTimeAsync(250)
    store.setSessionSearch('')
    await vi.advanceTimersByTimeAsync(250)

    const bucketCall = apiMock.get.mock.calls.filter(([, opts]) => opts.params.group_by !== 'session').at(-1)
    expect(bucketCall[1].params.session_ids).toBeUndefined()
  })

  it('drops a stale in-flight bucket response superseded by a newer request', async () => {
    respondForParams()
    const store = await getStore()
    await store.fetchData()
    apiMock.get.mockClear()

    let resolveFirst
    let resolveSecond
    apiMock.get.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    apiMock.get.mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve }))

    const flush = () => new Promise(resolve => setTimeout(resolve, 0))

    store.setModelFilter(['model-a'])
    store.setModelFilter(['model-b'])

    resolveSecond({ buckets: [{ bucket_ts: 200, by_token_type: {}, by_model: [] }] })
    await flush()
    resolveFirst({ buckets: [{ bucket_ts: 100, by_token_type: {}, by_model: [] }] })
    await flush()

    expect(store.buckets).toHaveLength(1)
    expect(store.buckets[0].bucket_ts).toBe(200)
  })
})
