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

  describe('patchSession (#1842)', () => {
    it('merges from response.session when present, not from the request payload', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-1', makeSession({ session_id: 'sess-1', template_id: null }))
      apiMock.patch.mockResolvedValue({
        success: true,
        session: makeSession({ session_id: 'sess-1', template_id: 'tmpl-server-confirmed' })
      })

      await store.patchSession('sess-1', { template_id: 'tmpl-requested' })

      // The backend's persisted value wins, not the outgoing request payload
      expect(store.sessions.get('sess-1').template_id).toBe('tmpl-server-confirmed')
    })

    it('falls back to the request payload if response.session is absent', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-1', makeSession({ session_id: 'sess-1', role: 'Old Role' }))
      apiMock.patch.mockResolvedValue({ success: true })

      await store.patchSession('sess-1', { role: 'New Role' })

      expect(store.sessions.get('sess-1').role).toBe('New Role')
    })

    it('does not clobber unrelated live fields (e.g. is_processing) not present in the request', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      // A poll-driven state_change event already flipped is_processing to false locally.
      store.sessions.set('sess-1', makeSession({ session_id: 'sess-1', is_processing: false, role: 'Old Role' }))
      // The PATCH response's session snapshot was captured before that happened, so it
      // still reflects the stale is_processing=true.
      apiMock.patch.mockResolvedValue({
        success: true,
        session: makeSession({ session_id: 'sess-1', is_processing: true, role: 'New Role' })
      })

      await store.patchSession('sess-1', { role: 'New Role' })

      expect(store.sessions.get('sess-1').role).toBe('New Role')
      expect(store.sessions.get('sess-1').is_processing).toBe(false)
    })

    it('falls back per-field to the request payload for keys the backend does not echo back', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      // Mirrors stores/polling.js's context_update handler, which patches ephemeral
      // display-only fields the backend does not persist or return.
      store.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
      apiMock.patch.mockResolvedValue({ success: true, message: 'No fields to update' })

      await store.patchSession('sess-1', { context_input_tokens: 42, context_window: 1000 })

      expect(store.sessions.get('sess-1').context_input_tokens).toBe(42)
      expect(store.sessions.get('sess-1').context_window).toBe(1000)
    })
  })

  describe('isUnreviewed active-session suppression (#1598)', () => {
    it('returns false for the currently selected session even when completion > viewed', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      const past = new Date(Date.now() - 10000).toISOString()
      const recent = new Date(Date.now() - 1000).toISOString()

      store.sessions.set('sess-active', makeSession({
        session_id: 'sess-active',
        last_completion_at: recent,
        last_viewed_at: past,
        is_processing: false
      }))
      store.currentSessionId = 'sess-active'

      expect(store.isUnreviewed('sess-active')).toBe(false)
    })

    it('returns true for a non-active session with unread completion', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      const past = new Date(Date.now() - 10000).toISOString()
      const recent = new Date(Date.now() - 1000).toISOString()

      store.sessions.set('sess-other', makeSession({
        session_id: 'sess-other',
        last_completion_at: recent,
        last_viewed_at: past,
        is_processing: false
      }))
      store.sessions.set('sess-active', makeSession({ session_id: 'sess-active' }))
      store.currentSessionId = 'sess-active'

      expect(store.isUnreviewed('sess-other')).toBe(true)
    })

    it('returns false for a non-active session when effectiveViewed >= last_completion_at', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      const recent = new Date(Date.now() - 1000).toISOString()
      const evenMoreRecent = new Date(Date.now() - 500).toISOString()

      store.sessions.set('sess-read', makeSession({
        session_id: 'sess-read',
        last_completion_at: recent,
        last_viewed_at: evenMoreRecent,
        is_processing: false
      }))
      store.sessions.set('sess-active', makeSession({ session_id: 'sess-active' }))
      store.currentSessionId = 'sess-active'

      expect(store.isUnreviewed('sess-read')).toBe(false)
    })
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

  describe('scroll position persistence — {itemIndex, offsetWithinItem} (#1748 stage: offset-model)', () => {
    it('round-trips a logical position through save/restore', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.saveScrollPosition('sess-a', { itemIndex: 42, offsetWithinItem: 17 })

      expect(store.scrollPositions.get('sess-a')).toEqual({ itemIndex: 42, offsetWithinItem: 17 })
    })

    it('round-trips restoring into a never-visited region (offsetWithinItem 0, high itemIndex)', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      // A large itemIndex with offsetWithinItem 0 is exactly what a never-visited jump target
      // looks like — the saved position only names an index, not a pixel a never-measured
      // region can't yet honestly report.
      store.saveScrollPosition('sess-b', { itemIndex: 9999, offsetWithinItem: 0 })

      expect(store.scrollPositions.get('sess-b')).toEqual({ itemIndex: 9999, offsetWithinItem: 0 })
    })

    it('ignores a raw pixel number (the pre-#1748 representation) instead of storing it', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.saveScrollPosition('sess-c', 1234) // old shape: a bare scrollTop pixel offset

      expect(store.scrollPositions.has('sess-c')).toBe(false)
    })

    it('ignores a missing/null position without throwing', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      expect(() => store.saveScrollPosition('sess-d', null)).not.toThrow()
      expect(store.scrollPositions.has('sess-d')).toBe(false)
    })
  })
})
