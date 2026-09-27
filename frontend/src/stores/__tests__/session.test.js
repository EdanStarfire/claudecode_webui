import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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
const pollingMock = vi.hoisted(() => ({
  connectSession: vi.fn().mockResolvedValue(undefined),
  disconnectSession: vi.fn(),
  cleanupSessionPollingState: vi.fn()
}))
vi.mock('@/stores/polling', () => ({
  usePollingStore: vi.fn(() => pollingMock)
}))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  pollingMock.connectSession.mockReset().mockResolvedValue(undefined)
  pollingMock.disconnectSession.mockReset()
  pollingMock.cleanupSessionPollingState.mockReset()
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

  it('deleteSession tears down the polling store\'s per-session state for every cascaded ID (#1974)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    store.sessions.set('sess-2', makeSession({ session_id: 'sess-2' }))

    apiMock.delete.mockResolvedValue({ deleted_session_ids: ['sess-1', 'sess-2'] })

    await store.deleteSession('sess-1')

    expect(pollingMock.cleanupSessionPollingState).toHaveBeenCalledWith('sess-1')
    expect(pollingMock.cleanupSessionPollingState).toHaveBeenCalledWith('sess-2')
    expect(pollingMock.cleanupSessionPollingState).toHaveBeenCalledTimes(2)
  })

  it('deleteSession disconnects the poll loop before the API call when deleting the currently displayed session (#1974)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    store.currentSessionId = 'sess-1'

    apiMock.delete.mockResolvedValue({ deleted_session_ids: ['sess-1'] })

    await store.deleteSession('sess-1')

    expect(pollingMock.disconnectSession).toHaveBeenCalled()
    // The whole point is to stop the poll loop before the session can disappear
    // server-side out from under it — not merely before this function returns.
    expect(pollingMock.disconnectSession.mock.invocationCallOrder[0])
      .toBeLessThan(apiMock.delete.mock.invocationCallOrder[0])
  })

  it('deleteSession does not disconnect the poll loop when deleting a different, non-current session (#1974)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    store.sessions.set('sess-2', makeSession({ session_id: 'sess-2' }))
    store.currentSessionId = 'sess-2'

    apiMock.delete.mockResolvedValue({ deleted_session_ids: ['sess-1'] })

    await store.deleteSession('sess-1')

    expect(pollingMock.disconnectSession).not.toHaveBeenCalled()
  })

  it('deleteSession disconnects the poll loop for a cascaded child that is the currently displayed session (#1974)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const store = useSessionStore()

    store.sessions.set('sess-parent', makeSession({ session_id: 'sess-parent' }))
    store.sessions.set('sess-child', makeSession({ session_id: 'sess-child' }))
    // The user is viewing the CHILD, but deletes the PARENT — the primary target isn't
    // current, so only the cascade-aware check (after deletedIds is known) catches this.
    store.currentSessionId = 'sess-child'

    apiMock.delete.mockResolvedValue({ deleted_session_ids: ['sess-parent', 'sess-child'] })

    await store.deleteSession('sess-parent')

    expect(pollingMock.disconnectSession).toHaveBeenCalled()
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

  describe('selectSession deep-link failure classification (#1977)', () => {
    it('a mocked 404 sets a permanent (not-found) deepLinkFailure', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useUIStore } = await import('@/stores/ui')
      const store = useSessionStore()
      const uiStore = useUIStore()

      const err = new Error('Not Found')
      err.status = 404
      apiMock.get.mockRejectedValue(err)

      await store.selectSession('sess-missing')

      expect(uiStore.deepLinkFailure).toEqual({
        sessionId: 'sess-missing',
        kind: 'not-found',
        message: 'Not Found',
      })
    })

    it('a mocked network/5xx error sets a transient deepLinkFailure', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useUIStore } = await import('@/stores/ui')
      const store = useSessionStore()
      const uiStore = useUIStore()

      const err = new Error('Internal Server Error')
      err.status = 500
      apiMock.get.mockRejectedValue(err)

      await store.selectSession('sess-flaky')

      expect(uiStore.deepLinkFailure).toEqual({
        sessionId: 'sess-flaky',
        kind: 'transient',
        message: 'Internal Server Error',
      })
    })

    it('a successful deep-link fetch clears a prior deepLinkFailure', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useUIStore } = await import('@/stores/ui')
      const store = useSessionStore()
      const uiStore = useUIStore()

      uiStore.setDeepLinkFailure({ sessionId: 'sess-1', kind: 'transient', message: 'oops' })
      apiMock.get.mockResolvedValue({ session: makeSession({ session_id: 'sess-1' }) })

      await store.selectSession('sess-1')

      expect(uiStore.deepLinkFailure).toBeNull()
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

  describe('hydration stage tracking (#2035)', () => {
    afterEach(async () => {
      // Restore the module's default per-call-fresh-object behavior in case a test
      // below overrode it with a fixed mockImplementation.
      const { useResourceStore } = await import('@/stores/resource')
      useResourceStore.mockImplementation(() => ({ loadResources: vi.fn().mockResolvedValue(undefined) }))
    })

    it('happy path progresses to ready and connects polling', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-hydrate', makeSession({ session_id: 'sess-hydrate', state: 'active' }))
      apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false, agents: [] })

      await store.selectSession('sess-hydrate')

      const entry = store.hydrationStageBySession.get('sess-hydrate')
      expect(entry.stage).toBe('ready')
      expect(pollingMock.connectSession).toHaveBeenCalledWith('sess-hydrate')
    })

    it('loadMessages() throwing surfaces a thrown error at loading_history and never connects polling', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-throw', makeSession({ session_id: 'sess-throw', state: 'active' }))
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/messages')) return Promise.reject(new Error('boom'))
        return Promise.resolve({ agents: [] })
      })

      await store.selectSession('sess-throw')

      const entry = store.hydrationStageBySession.get('sess-throw')
      expect(entry.stage).toBe('error')
      expect(entry.error).toMatchObject({ stage: 'loading_history', kind: 'thrown', message: 'boom' })
      expect(pollingMock.connectSession).not.toHaveBeenCalled()
    })

    it('loadMessages() hanging past 20s surfaces a timeout error', async () => {
      vi.useFakeTimers()
      try {
        const { useSessionStore } = await import('@/stores/session')
        const store = useSessionStore()

        store.sessions.set('sess-hang', makeSession({ session_id: 'sess-hang', state: 'active' }))
        apiMock.get.mockImplementation((url) => {
          if (url.includes('/messages')) return new Promise(() => {}) // never resolves
          return Promise.resolve({ agents: [] })
        })

        const selectPromise = store.selectSession('sess-hang')
        await vi.advanceTimersByTimeAsync(20000)
        await selectPromise

        const entry = store.hydrationStageBySession.get('sess-hang')
        expect(entry.stage).toBe('error')
        expect(entry.error.kind).toBe('timeout')
      } finally {
        vi.useRealTimers()
      }
    })

    it('aborts a stale selection, clearing its stage entry, when switching sessions mid-hydration', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-a', makeSession({ session_id: 'sess-a', state: 'active' }))
      store.sessions.set('sess-b', makeSession({ session_id: 'sess-b', state: 'active' }))

      let resolveAMessages
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/sessions/sess-a/messages')) {
          return new Promise((resolve) => { resolveAMessages = resolve })
        }
        return Promise.resolve({ messages: [], total_count: 0, has_more: false, agents: [] })
      })

      const selectA = store.selectSession('sess-a')
      await vi.waitFor(() => {
        expect(store.hydrationStageBySession.get('sess-a')?.stage).toBe('loading_history')
      })

      const selectB = store.selectSession('sess-b')
      resolveAMessages?.({ messages: [], total_count: 0, has_more: false })
      await Promise.all([selectA, selectB])

      expect(store.hydrationStageBySession.has('sess-a')).toBe(false)
      expect(store.hydrationStageBySession.get('sess-b')?.stage).toBe('ready')
    })

    it('retryHydration resumes from the failed step without re-invoking already-succeeded steps', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useResourceStore } = await import('@/stores/resource')
      const store = useSessionStore()

      store.sessions.set('sess-retry', makeSession({ session_id: 'sess-retry', state: 'active' }))
      apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false, agents: [] })

      // Fix useResourceStore()'s return value across calls (its default mock factory
      // otherwise hands back a fresh loadResources spy on every call), so the rejection
      // configured here is what both the initial attempt and the retry actually see.
      const loadResourcesMock = vi.fn()
      loadResourcesMock.mockRejectedValueOnce(new Error('network down'))
      loadResourcesMock.mockResolvedValue(undefined)
      useResourceStore.mockImplementation(() => ({ loadResources: loadResourcesMock }))

      await store.selectSession('sess-retry')

      let entry = store.hydrationStageBySession.get('sess-retry')
      expect(entry.stage).toBe('error')
      expect(entry.error.stage).toBe('loading_resources')

      const messagesCallsAfterFirstAttempt = apiMock.get.mock.calls.filter(([url]) => url.includes('/messages')).length
      expect(messagesCallsAfterFirstAttempt).toBe(1)

      await store.retryHydration('sess-retry')

      entry = store.hydrationStageBySession.get('sess-retry')
      expect(entry.stage).toBe('ready')
      const messagesCallsAfterRetry = apiMock.get.mock.calls.filter(([url]) => url.includes('/messages')).length
      expect(messagesCallsAfterRetry).toBe(1) // not re-invoked by the retry — already succeeded
    })

    it('re-selecting the already-current session leaves a prior ready stage untouched', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-same', makeSession({ session_id: 'sess-same', state: 'active' }))
      apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false, agents: [] })

      await store.selectSession('sess-same')
      expect(store.hydrationStageBySession.get('sess-same')?.stage).toBe('ready')

      await store.selectSession('sess-same')
      expect(store.hydrationStageBySession.get('sess-same')?.stage).toBe('ready')
    })

    it('clears a stale error entry as soon as a fresh selection begins, before the new chain even resolves (builder-review fix)', async () => {
      // Regression coverage for: reset/restart callers null out currentSessionId then
      // re-call selectSession() for the same id (bypassing the wasAlreadyCurrent
      // early-return) specifically to force a fresh attempt. Without clearing the old
      // entry up front, the previous error/Retry banner would linger through this new
      // attempt, and clicking that stale Retry could call retryHydration() with a
      // controller that aborts the very selection now in flight.
      const { useSessionStore } = await import('@/stores/session')
      const store = useSessionStore()

      store.sessions.set('sess-stale', makeSession({ session_id: 'sess-stale', state: 'active' }))
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/messages')) return Promise.reject(new Error('boom'))
        return Promise.resolve({ agents: [] })
      })

      await store.selectSession('sess-stale')
      expect(store.hydrationStageBySession.get('sess-stale')?.stage).toBe('error')

      // Mimic the reset/restart pattern: null out currentSessionId first.
      store.currentSessionId = null

      let resolveMessages
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/messages')) return new Promise((resolve) => { resolveMessages = resolve })
        return Promise.resolve({ agents: [] })
      })
      const reselect = store.selectSession('sess-stale')

      // The stale error is gone: the fresh attempt reaches a genuine loading_history
      // stage instead of the old selection's 'error' entry lingering in its place.
      await vi.waitFor(() => {
        expect(store.hydrationStageBySession.get('sess-stale')?.stage).toBe('loading_history')
      })
      expect(store.hydrationStageBySession.get('sess-stale')?.error).toBeNull()

      resolveMessages?.({ messages: [], total_count: 0, has_more: false, agents: [] })
      await reselect

      expect(store.hydrationStageBySession.get('sess-stale')?.stage).toBe('ready')
    })
  })
})
