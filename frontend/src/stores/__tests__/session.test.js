import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
vi.mock('@/stores/resource', () => ({
  useResourceStore: vi.fn(() => ({ loadResources: vi.fn().mockResolvedValue(undefined) }))
}))
vi.mock('@/stores/usage', () => ({
  useUsageStore: vi.fn(() => ({ loadUsage: vi.fn() }))
}))
vi.mock('@/stores/polling', () => ({
  usePollingStore: vi.fn(() => ({
    connectSession: vi.fn().mockResolvedValue(undefined),
    disconnectSession: vi.fn()
  }))
}))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('session store', () => {
  it('fetchSessions merges into existing Map without losing references', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    const existing = makeSession({ session_id: 'sess-1', name: 'Old Name' })
    store.sessions.set('sess-1', existing)

    const updated = makeSession({ session_id: 'sess-1', name: 'New Name' })
    const fresh = makeSession({ session_id: 'sess-2', name: 'Fresh' })
    apiMock.get.mockResolvedValue({ sessions: [updated, fresh] })

    await store.fetchSessions()

    expect(store.sessions.size).toBe(2)
    // Object.assign semantics: name merged from updated session
    expect(store.sessions.get('sess-1').name).toBe('New Name')
    // Session that was not in the return set is removed
    expect(store.sessions.has('sess-2')).toBe(true)
  })

  it('createSession posts and adds session to map', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    apiMock.post.mockResolvedValue({ session_id: 'sess-2' })
    apiMock.get.mockResolvedValue({ session: makeSession({ session_id: 'sess-2' }) })

    const session = await store.createSession('proj-1', { name: 'New' })

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ project_id: 'proj-1' }))
    expect(store.sessions.has('sess-2')).toBe(true)
    expect(session.session_id).toBe('sess-2')
  })

  it('selectSession early-returns when already current and not selecting', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()
    store.currentSessionId = 'sess-1'

    await store.selectSession('sess-1')

    expect(apiMock.get).not.toHaveBeenCalled()
  })

  it('deleteSession removes all cascaded IDs and clears currentSessionId', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    store.sessions.set('sess-2', makeSession({ session_id: 'sess-2' }))
    store.currentSessionId = 'sess-1'

    apiMock.delete.mockResolvedValue({ deleted_session_ids: ['sess-1', 'sess-2'] })

    await store.deleteSession('sess-1')

    expect(store.sessions.has('sess-1')).toBe(false)
    expect(store.sessions.has('sess-2')).toBe(false)
    expect(store.currentSessionId).toBeNull()
  })

  it('getInput/setInput caches per session', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.setInput('sess-1', 'hello')

    expect(store.getInput('sess-2')).toBe('')
    expect(store.getInput('sess-1')).toBe('hello')
  })

  describe('selectSession cache gate (#1515)', () => {
    it('calls loadMessages on first visit when session is not cached', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-fresh', makeSession({ session_id: 'sess-fresh', state: 'active' }))
      apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })

      await store.selectSession('sess-fresh')

      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/sessions/sess-fresh/messages')
      )
    })

    it('skips loadMessages on switch-back when session messages are already cached', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const store = useSessionStore()
      const msgStore = useMessageStore()

      store.sessions.set('sess-cached', makeSession({ session_id: 'sess-cached', state: 'active' }))
      msgStore.messagesBySession.set('sess-cached', [])

      await store.selectSession('sess-cached')

      expect(apiMock.get).not.toHaveBeenCalledWith(
        expect.stringContaining('/api/sessions/sess-cached/messages')
      )
    })
  })
})
