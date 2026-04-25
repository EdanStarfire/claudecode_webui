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

  it('currentInput getter/setter caches per session', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.currentSessionId = 'sess-1'
    store.currentInput = 'hello'

    store.currentSessionId = 'sess-2'
    expect(store.currentInput).toBe('')

    store.currentSessionId = 'sess-1'
    expect(store.currentInput).toBe('hello')
  })
})
