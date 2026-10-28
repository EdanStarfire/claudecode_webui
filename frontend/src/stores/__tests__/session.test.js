import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeSession, makeMessage } from '@/test-utils/factories'

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
  useResourceStore: vi.fn(() => ({
    loadResources: vi.fn().mockResolvedValue(undefined),
    clearResources: vi.fn()
  }))
}))
vi.mock('@/stores/usage', () => ({
  useUsageStore: vi.fn(() => ({ loadUsage: vi.fn() }))
}))
const pollingMock = vi.hoisted(() => ({
  connectSession: vi.fn().mockResolvedValue(undefined),
  disconnectSession: vi.fn(),
  resetSessionCursor: vi.fn()
}))
vi.mock('@/stores/polling', () => ({
  usePollingStore: vi.fn(() => pollingMock)
}))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  pollingMock.connectSession.mockReset().mockResolvedValue(undefined)
  pollingMock.disconnectSession.mockReset()
  pollingMock.resetSessionCursor.mockReset()
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

    expect(pollingMock.resetSessionCursor).toHaveBeenCalledWith('sess-1')
    expect(pollingMock.resetSessionCursor).toHaveBeenCalledWith('sess-2')
    expect(pollingMock.resetSessionCursor).toHaveBeenCalledTimes(2)
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

  describe('removeSessionsFromStores scope widening (issue #2065 AC6)', () => {
    it('clears messages, resources, edit history, queue, task, and diff state for the removed session', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const { useResourceStore } = await import('@/stores/resource')
      const { useEditHistoryStore } = await import('@/stores/editHistory')
      const { useQueueStore } = await import('@/stores/queue')
      const { useTaskStore } = await import('@/stores/task')
      const { useDiffStore } = await import('@/stores/diff')

      const store = useSessionStore()
      const messageStore = useMessageStore()
      // Fix useResourceStore()'s return value across calls (its default mock factory
      // otherwise hands back a fresh clearResources spy on every call) — mirrors the
      // existing pattern above for selectSession's own loadResources assertions.
      const resourceStore = { loadResources: vi.fn().mockResolvedValue(undefined), clearResources: vi.fn() }
      useResourceStore.mockImplementation(() => resourceStore)
      const editHistoryStore = useEditHistoryStore()
      const queueStore = useQueueStore()
      const taskStore = useTaskStore()
      const diffStore = useDiffStore()

      const sid = 'sess-ac6'
      store.sessions.set(sid, makeSession({ session_id: sid }))

      // Seed per-session state directly in each store's own Map/Set.
      messageStore.messagesBySession.set(sid, [{ id: 'm1', type: 'user' }])
      editHistoryStore.entriesBySession.set(sid, [{ id: 'e1' }])
      queueStore.queuesBySession.set(sid, [{ queue_id: 'q1' }])
      queueStore.pausedBySession.set(sid, true)
      taskStore.tasksBySession.set(sid, new Map([['t1', { id: 't1' }]]))
      taskStore.sessionsWithTasks.add(sid)
      diffStore.diffBySession.set(sid, { files: ['a.js'] })

      await store.removeSessionsFromStores([sid])

      expect(messageStore.messagesBySession.has(sid)).toBe(false)
      expect(resourceStore.clearResources).toHaveBeenCalledWith(sid)
      expect(editHistoryStore.entriesBySession.has(sid)).toBe(false)
      expect(queueStore.queuesBySession.has(sid)).toBe(false)
      expect(queueStore.pausedBySession.has(sid)).toBe(false)
      expect(taskStore.tasksBySession.has(sid)).toBe(false)
      expect(taskStore.sessionsWithTasks.has(sid)).toBe(false)
      expect(diffStore.diffBySession.has(sid)).toBe(false)
    })

    it('only clears the rate-limit display when the removed session was the current one', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useUIStore } = await import('@/stores/ui')

      const store = useSessionStore()
      const uiStore = useUIStore()

      store.sessions.set('sess-current', makeSession({ session_id: 'sess-current' }))
      store.sessions.set('sess-background', makeSession({ session_id: 'sess-background' }))
      store.currentSessionId = 'sess-current'
      uiStore.setRateLimits({ requests_remaining: 10 })

      // Deleting a different, non-current (background) session must not touch the
      // currently-displayed session's rate-limit badge.
      await store.removeSessionsFromStores(['sess-background'])
      expect(uiStore.rateLimits).toEqual({ requests_remaining: 10 })

      // Deleting the current session does clear it.
      await store.removeSessionsFromStores(['sess-current'])
      expect(uiStore.rateLimits).toBeNull()
    })

    it('clears session state synchronously, before the returned promise settles (issue #2076 AC8)', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const store = useSessionStore()
      const messageStore = useMessageStore()

      const sid = 'sess-sync'
      store.sessions.set(sid, makeSession({ session_id: sid }))
      messageStore.messagesBySession.set(sid, [{ id: 'm1', type: 'user' }])

      const promise = store.removeSessionsFromStores([sid])
      // Deliberately NOT awaited yet. If removeSessionsFromStores() still had any internal
      // `await import(...)`, the function would have suspended at the first one and control
      // would already be back here — before the mutation below ran — making this assertion
      // fail intermittently depending on microtask timing. With zero internal awaits, the
      // function body runs to completion (including this mutation) before even returning,
      // so this holds deterministically every time.
      expect(messageStore.messagesBySession.has(sid)).toBe(false)

      await promise
    })
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

      // Issue #2140: the paginated path now passes a second { signal } options arg to api.get().
      expect(apiMock.get).toHaveBeenCalledWith(
        expect.stringContaining('/api/sessions/sess-fresh/messages'), expect.anything()
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

    it('loadMessages() first page hanging past the 30s per-page inactivity window surfaces a timeout error (#2140)', async () => {
      // Issue #2140: loadMessages() no longer has a whole-call timeout (HYDRATION_STEP_TIMEOUT_MS
      // / withTimeout() was removed for this step) — a stalled page is now caught by the
      // per-page HISTORY_PAGE_INACTIVITY_TIMEOUT_MS (30s) inactivity timer instead, which aborts
      // only that page's own fetch via the signal threaded through api.get().
      vi.useFakeTimers()
      try {
        const { useSessionStore } = await import('@/stores/session')
        const store = useSessionStore()

        store.sessions.set('sess-hang', makeSession({ session_id: 'sess-hang', state: 'active' }))
        apiMock.get.mockImplementation((url, options) => {
          if (url.includes('/messages')) {
            // Abort-aware hang, mirroring what a real fetch() does: never resolves on its
            // own, but rejects once the per-page inactivity timer aborts its signal.
            return new Promise((_resolve, reject) => {
              options?.signal?.addEventListener('abort', () => {
                const err = new Error('Aborted')
                err.name = 'AbortError'
                reject(err)
              })
            })
          }
          return Promise.resolve({ agents: [] })
        })

        const selectPromise = store.selectSession('sess-hang')
        await vi.advanceTimersByTimeAsync(30000)
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
      const store = useSessionStore()

      store.sessions.set('sess-retry', makeSession({ session_id: 'sess-retry', state: 'active' }))
      // The history window fails once, then succeeds. (The resources fetch is no longer a
      // blocking hydration step — see the dedicated test below — so the window is the step
      // that can fail and be retried.)
      let windowCalls = 0
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/messages')) {
          windowCalls++
          return windowCalls === 1
            ? Promise.reject(new Error('network down'))
            : Promise.resolve({ messages: [], start_cursor: 'b:0', has_more_before: false, history_id: 'h', total_bytes: 0 })
        }
        return Promise.resolve({ agents: [], tasks: [] })
      })

      await store.selectSession('sess-retry')

      let entry = store.hydrationStageBySession.get('sess-retry')
      expect(entry.stage).toBe('error')
      expect(entry.error.stage).toBe('loading_history')
      expect(pollingMock.connectSession).not.toHaveBeenCalled()

      await store.retryHydration('sess-retry')

      entry = store.hydrationStageBySession.get('sess-retry')
      expect(entry.stage).toBe('ready')
      expect(windowCalls).toBe(2)
      expect(pollingMock.connectSession).toHaveBeenCalledWith('sess-retry')
      // Already-succeeded work is not repeated by a second retry of a ready session.
      await store.retryHydration('sess-retry')
      expect(windowCalls).toBe(2)
    })

    it('a slow or failing resources fetch does not hold the session out of ready (issue #2030)', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useResourceStore } = await import('@/stores/resource')
      const store = useSessionStore()

      store.sessions.set('sess-res', makeSession({ session_id: 'sess-res', state: 'active' }))
      apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false, agents: [] })
      useResourceStore.mockImplementation(() => ({ loadResources: vi.fn().mockReturnValue(new Promise(() => {})) }))

      await store.selectSession('sess-res')

      expect(store.hydrationStageBySession.get('sess-res').stage).toBe('ready')
      expect(pollingMock.connectSession).toHaveBeenCalledWith('sess-res')

      useResourceStore.mockImplementation(() => ({ loadResources: vi.fn().mockRejectedValue(new Error('boom')) }))
      store.currentSessionId = null
      await store.selectSession('sess-res')
      expect(store.hydrationStageBySession.get('sess-res').stage).toBe('ready')
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

  describe('tail-first history load — window/abort/retry (issues #2140, #2030)', () => {
    it('T1: the window loads in ONE messages request, in parallel with the agents snapshot, and is installed before connect', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const store = useSessionStore()
      const msgStore = useMessageStore()
      store.sessions.set('sess-t1', makeSession({ session_id: 'sess-t1', state: 'active' }))

      const order = []
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/background_agents')) { order.push('agents'); return Promise.resolve({ agents: [] }) }
        if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
        if (url.includes('/tasks')) { order.push('tasks'); return Promise.resolve({ tasks: [] }) }
        if (url.includes('/messages')) {
          order.push('window')
          return Promise.resolve({
            messages: [makeMessage({ content: 'newest' })],
            start_cursor: 'b:900', has_more_before: true, history_id: 'h1', snapped: true, total_bytes: 1000,
            latest_init: { model: 'm' }, event_cursor: 7, epoch: 'e1',
          })
        }
        return Promise.resolve({})
      })
      pollingMock.connectSession.mockImplementation(async () => { order.push('connect') })

      await store.selectSession('sess-t1')

      const messageRequests = apiMock.get.mock.calls.filter(([url]) => url.includes('/messages'))
      expect(messageRequests).toHaveLength(1)
      expect(messageRequests[0][0]).toBe('/api/sessions/sess-t1/messages?window=tail')
      // Both first-stage requests are issued before either completes; /tasks only after the
      // window response; connect only after the window and the agents snapshot.
      expect(order.indexOf('window')).toBeLessThan(order.indexOf('tasks'))
      expect(order.indexOf('agents')).toBeLessThan(order.indexOf('connect'))
      expect(msgStore.messagesBySession.get('sess-t1').map(m => m.content)).toEqual(['newest'])
      expect(msgStore.isHistoryComplete('sess-t1')).toBe(false)
      expect(store.hydrationStageBySession.get('sess-t1').stage).toBe('ready')
    })

    it('T2/T3: a stalled window request times out naming loading_history; retryHydration() re-fetches it', async () => {
      vi.useFakeTimers()
      try {
        const { useSessionStore } = await import('@/stores/session')
        const { useMessageStore } = await import('@/stores/message')
        const store = useSessionStore()
        const msgStore = useMessageStore()
        store.sessions.set('sess-t2', makeSession({ session_id: 'sess-t2', state: 'active' }))

        apiMock.get.mockImplementation((url, options) => {
          if (url.includes('/background_agents')) return Promise.resolve({ agents: [] })
          if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
          if (url.includes('/tasks')) return Promise.resolve({ tasks: [] })
          return new Promise((_resolve, reject) => {
            options?.signal?.addEventListener('abort', () => {
              const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
            })
          })
        })

        const selectPromise = store.selectSession('sess-t2')
        await vi.advanceTimersByTimeAsync(30000)
        await selectPromise

        let entry = store.hydrationStageBySession.get('sess-t2')
        expect(entry.stage).toBe('error')
        expect(entry.error.kind).toBe('timeout')
        expect(entry.error.stage).toBe('loading_history')
        expect(msgStore.messagesBySession.has('sess-t2')).toBe(false)

        apiMock.get.mockImplementation((url) => {
          if (url.includes('/background_agents')) return Promise.resolve({ agents: [] })
          if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
          if (url.includes('/tasks')) return Promise.resolve({ tasks: [] })
          return Promise.resolve({ messages: [makeMessage({ content: 'after-retry' })], start_cursor: 'b:0', has_more_before: false, history_id: 'h', total_bytes: 1 })
        })
        await store.retryHydration('sess-t2')

        entry = store.hydrationStageBySession.get('sess-t2')
        expect(entry.stage).toBe('ready')
        expect(msgStore.messagesBySession.get('sess-t2').map(m => m.content)).toEqual(['after-retry'])
      } finally {
        vi.useRealTimers()
      }
    })

    it('two rapid retryHydration() calls result in the first attempt being aborted before the second starts (no concurrent double-fetch)', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const store = useSessionStore()
      const msgStore = useMessageStore()
      store.sessions.set('sess-concurrent', makeSession({ session_id: 'sess-concurrent', state: 'active' }))

      const capturedSignals = []
      apiMock.get.mockImplementation((url, options) => {
        if (url.includes('/background_agents')) return Promise.resolve({ agents: [] })
        if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
        capturedSignals.push(options?.signal)
        return new Promise((_resolve, reject) => {
          options?.signal?.addEventListener('abort', () => {
            const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
          })
        })
      })

      const firstSelect = store.selectSession('sess-concurrent')
      await vi.waitFor(() => {
        expect(msgStore.hasPendingHistoryLoad('sess-concurrent')).toBe(true)
      })

      const retry1 = store.retryHydration('sess-concurrent')
      await vi.waitFor(() => {
        expect(capturedSignals.length).toBeGreaterThanOrEqual(2)
      })

      // A second, rapid retry claims ownership again before retry1's own attempt ever
      // succeeds — retry1's just-claimed controller must be the one aborted.
      apiMock.get.mockImplementation((url) => {
        if (url.includes('/background_agents')) return Promise.resolve({ agents: [] })
        if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
        return Promise.resolve({ messages: [], total_count: 0, has_more: false, epoch: 'e1' })
      })
      await store.retryHydration('sess-concurrent')
      await Promise.allSettled([firstSelect, retry1])

      expect(capturedSignals.length).toBeGreaterThanOrEqual(2)
      // Every attempt before the final, successful one was aborted — exactly one attempt
      // ever reaches a genuinely live (non-aborted) fetch; no doubled network passes.
      expect(capturedSignals.slice(0, -1).every(s => s.aborted)).toBe(true)
      expect(store.hydrationStageBySession.get('sess-concurrent')?.stage).toBe('ready')
    })

    it('switching away from a session whose history load is stuck, then back, aborts the orphaned attempt instead of running two loops concurrently (builder-review fix)', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const store = useSessionStore()
      const msgStore = useMessageStore()
      store.sessions.set('sess-switch-a', makeSession({ session_id: 'sess-switch-a', state: 'active' }))
      store.sessions.set('sess-switch-b', makeSession({ session_id: 'sess-switch-b', state: 'active' }))

      // Only the FIRST request for A's messages hangs (simulating the stuck load) — a
      // SECOND request (the re-entry's own, fresh attempt) resolves normally. Changing
      // apiMock's implementation mid-test wouldn't affect a promise a prior call already
      // returned, so this has to be done via a counter instead of a later mock swap.
      const capturedSignalsA = []
      let sessAMessagesCallCount = 0
      apiMock.get.mockImplementation((url, options) => {
        if (url.includes('/background_agents')) return Promise.resolve({ agents: [] })
        if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
        if (url.includes('sess-switch-a') && url.includes('/messages')) {
          capturedSignalsA.push(options?.signal)
          sessAMessagesCallCount++
          if (sessAMessagesCallCount === 1) {
            return new Promise((_resolve, reject) => {
              options?.signal?.addEventListener('abort', () => {
                const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
              })
            })
          }
        }
        return Promise.resolve({ messages: [], total_count: 0, has_more: false, epoch: 'e1' })
      })

      const selectA = store.selectSession('sess-switch-a')
      await vi.waitFor(() => {
        expect(msgStore.hasPendingHistoryLoad('sess-switch-a')).toBe(true)
      })

      // Switch away (A's load is left running, orphaned — not the current selection
      // anymore) and then immediately back, while A's first attempt is still stuck.
      await store.selectSession('sess-switch-b')
      const selectAAgain = store.selectSession('sess-switch-a')
      await vi.waitFor(() => {
        expect(capturedSignalsA.length).toBeGreaterThanOrEqual(2)
      })

      // The FIRST attempt's signal must already be aborted by the re-entry's claim —
      // otherwise two fetchAllMessagePages() loops would run concurrently over the same
      // shared state, each making its own redundant network request.
      expect(capturedSignalsA[0].aborted).toBe(true)

      await Promise.allSettled([selectA, selectAAgain])

      expect(store.hydrationStageBySession.get('sess-switch-a')?.stage).toBe('ready')
    })

    it('a concurrent claim stealing this attempt\'s own controller (distinct from this selection\'s outer abort) does not surface a false hydration error (builder-review fix)', async () => {
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const store = useSessionStore()
      const msgStore = useMessageStore()
      store.sessions.set('sess-stolen', makeSession({ session_id: 'sess-stolen', state: 'active' }))

      apiMock.get.mockImplementation((url, options) => {
        if (url.includes('/background_agents')) return Promise.resolve({ agents: [] })
        if (url.includes('/background_tasks')) return Promise.resolve({ tasks: [] })
        return new Promise((_resolve, reject) => {
          options?.signal?.addEventListener('abort', () => {
            const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
          })
        })
      })

      const selectPromise = store.selectSession('sess-stolen')
      await vi.waitFor(() => {
        expect(msgStore.hasPendingHistoryLoad('sess-stolen')).toBe(true)
      })

      // Something ELSE (e.g. polling.js's resync) steals the claim — this selection's OWN
      // outer abortController is never touched.
      msgStore.claimHydrationLoad('sess-stolen')
      await selectPromise

      // Pre-empted, not failed: no error banner left behind for the user to see/retry.
      expect(store.hydrationStageBySession.has('sess-stolen')).toBe(false)
    })
  })
})
