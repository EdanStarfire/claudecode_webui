import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('queue store', () => {
  describe('removeSessionQueue (issue #2065 AC6)', () => {
    it('removes the session from queuesBySession, pausedBySession, and paginationBySession', async () => {
      const { useQueueStore } = await import('@/stores/queue')
      const store = useQueueStore()

      store.queuesBySession.set('sess-1', [{ queue_id: 'q1' }])
      store.pausedBySession.set('sess-1', true)
      store.paginationBySession.set('sess-1', { offset: 1, hasMore: true, total: 1, pendingCount: 1 })

      store.removeSessionQueue('sess-1')

      expect(store.queuesBySession.has('sess-1')).toBe(false)
      expect(store.pausedBySession.has('sess-1')).toBe(false)
      expect(store.paginationBySession.has('sess-1')).toBe(false)
    })

    it('does not make any REST call, unlike clearQueue()/clearHistory()', async () => {
      const { useQueueStore } = await import('@/stores/queue')
      const store = useQueueStore()

      store.queuesBySession.set('sess-1', [{ queue_id: 'q1' }])
      store.pausedBySession.set('sess-1', false)

      store.removeSessionQueue('sess-1')

      expect(apiMock.delete).not.toHaveBeenCalled()
      expect(apiMock.get).not.toHaveBeenCalled()
      expect(apiMock.post).not.toHaveBeenCalled()
      expect(apiMock.put).not.toHaveBeenCalled()
    })

    it('is a no-op for a session with no queue state', async () => {
      const { useQueueStore } = await import('@/stores/queue')
      const store = useQueueStore()

      expect(() => store.removeSessionQueue('sess-missing')).not.toThrow()
      expect(store.queuesBySession.has('sess-missing')).toBe(false)
    })
  })
})
