import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeComm, makeMinion } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('legion store', () => {
  it('sendComm posts to backend and returns comm', async () => {
    const { useLegionStore } = await import('@/stores/legion')
    const store = useLegionStore()

    const comm = makeComm({ comm_id: 'c-1' })
    apiMock.post.mockResolvedValue({ comm })

    const result = await store.sendComm('leg-1', { summary: 'test', content: 'hello' })

    expect(apiMock.post).toHaveBeenCalledWith('/api/legions/leg-1/comms', expect.any(Object))
    expect(result.comm_id).toBe('c-1')
  })

  it('createMinion posts to backend and returns minion', async () => {
    const { useLegionStore } = await import('@/stores/legion')
    const store = useLegionStore()

    const minion = makeMinion({ minion_id: 'm-1' })
    apiMock.post.mockResolvedValue({ minion })

    const result = await store.createMinion('leg-1', { name: 'Worker', role: 'worker' })

    expect(apiMock.post).toHaveBeenCalledWith('/api/legions/leg-1/minions', expect.any(Object))
    expect(result.minion_id).toBe('m-1')
  })

  it('haltAll posts to halt-all endpoint and returns new shape', async () => {
    const { useLegionStore } = await import('@/stores/legion')
    const store = useLegionStore()

    apiMock.post.mockResolvedValue({
      stopped_session_ids: ['s-1', 's-2'],
      failed_sessions: [],
      total_sessions: 2,
    })

    const result = await store.haltAll('leg-1')

    expect(apiMock.post).toHaveBeenCalledWith('/api/legions/leg-1/halt-all')
    expect(result.stopped_session_ids).toEqual(['s-1', 's-2'])
    expect(result.failed_sessions).toEqual([])
    expect(result.total_sessions).toBe(2)
  })
})
