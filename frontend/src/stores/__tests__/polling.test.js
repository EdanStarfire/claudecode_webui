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
vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn().mockReturnValue(null)
}))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('polling store', () => {
  it('sendMessage POSTs to correct sessions endpoint', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.currentSessionId = 'sess-1'
    apiMock.post.mockResolvedValue({})

    await pollingStore.sendMessage('hello world')

    expect(apiMock.post).toHaveBeenCalledWith(
      '/api/sessions/sess-1/messages',
      expect.objectContaining({ message: 'hello world' })
    )
  })

  it('interruptSession POSTs to interrupt endpoint', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.currentSessionId = 'sess-1'
    apiMock.post.mockResolvedValue({})

    await pollingStore.interruptSession()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/sess-1/interrupt', {})
  })

  it('sendPermissionResponse POSTs with correct payload', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.currentSessionId = 'sess-1'
    apiMock.post.mockResolvedValue({})

    await pollingStore.sendPermissionResponse('req-1', 'allow', false)

    expect(apiMock.post).toHaveBeenCalledWith(
      '/api/sessions/sess-1/permission/req-1',
      expect.objectContaining({ decision: 'allow' })
    )
  })

  it('stopUIPolling sets uiConnected to false', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    pollingStore.uiConnected = true
    pollingStore.stopUIPolling()

    expect(pollingStore.uiConnected).toBe(false)
  })

  // Helper: drive a sequence of state_change events (each item is a session state
  // override) through startUIPolling() in a single poll response, then hang until
  // stopUIPolling() aborts the fetch.
  async function runStateChangeSequence(sessionId, stateOverridesList) {
    apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })

    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    let callCount = 0
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
      callCount++
      if (callCount === 1) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            events: stateOverridesList.map(overrides => ({
              type: 'state_change',
              data: {
                session_id: sessionId,
                session: makeSession({ session_id: sessionId, ...overrides })
              }
            })),
            next_cursor: 1
          })
        })
      }
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    })

    pollingStore.startUIPolling()
    await new Promise(resolve => setTimeout(resolve, 0))
    pollingStore.stopUIPolling()
  }

  it('error state reload clears cache and cursor before calling loadMessages (#1515)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { useSessionStore } = await import('@/stores/session')

    const msgStore = useMessageStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-e', makeSession({ session_id: 'sess-e', state: 'active' }))
    msgStore.messagesBySession.set('sess-e', [])

    const clearSpy = vi.spyOn(msgStore, 'clearMessages')
    const loadSpy = vi.spyOn(msgStore, 'loadMessages')

    await runStateChangeSequence('sess-e', [{ state: 'error' }])

    expect(clearSpy).toHaveBeenCalledWith('sess-e')
    expect(loadSpy).toHaveBeenCalledWith('sess-e')
    expect(clearSpy.mock.invocationCallOrder[0]).toBeLessThan(loadSpy.mock.invocationCallOrder[0])
  })

  it('does not re-notify when a session already in error state reports error again (#1731)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const { notify } = await import('@/composables/useNotifications')
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-e', makeSession({ session_id: 'sess-e', state: 'error' }))

    await runStateChangeSequence('sess-e', [{ state: 'error' }])

    expect(notify).not.toHaveBeenCalledWith('session_error', expect.anything())
  })

  it('notifies once when a session transitions from active to error for the first time (#1731)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const { notify } = await import('@/composables/useNotifications')
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-e', makeSession({ session_id: 'sess-e', state: 'active' }))

    await runStateChangeSequence('sess-e', [{ state: 'error' }])

    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify).toHaveBeenCalledWith('session_error', expect.objectContaining({ sessionId: 'sess-e' }))
  })

  it('notifies once when a session transitions from active to paused for the first time (#1731)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const { notify } = await import('@/composables/useNotifications')
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-p', makeSession({ session_id: 'sess-p', state: 'active' }))

    await runStateChangeSequence('sess-p', [{ state: 'paused' }])

    expect(notify).toHaveBeenCalledTimes(1)
    expect(notify).toHaveBeenCalledWith('permission_prompt', expect.objectContaining({ sessionId: 'sess-p' }))
  })

  it('does not re-notify when a session already paused reports paused again (#1731)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const { notify } = await import('@/composables/useNotifications')
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-p', makeSession({ session_id: 'sess-p', state: 'paused' }))

    await runStateChangeSequence('sess-p', [{ state: 'paused' }])

    expect(notify).not.toHaveBeenCalledWith('permission_prompt', expect.anything())
  })

  it('notifies twice when a session errors, recovers, then errors again (#1731)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const { notify } = await import('@/composables/useNotifications')
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-e', makeSession({ session_id: 'sess-e', state: 'active' }))

    await runStateChangeSequence('sess-e', [
      { state: 'error' },
      { state: 'active' },
      { state: 'error' }
    ])

    const errorCalls = notify.mock.calls.filter(call => call[0] === 'session_error')
    expect(errorCalls).toHaveLength(2)
  })

  it('continues to notify task_complete exactly once per is_processing transition (#1731 regression check)', async () => {
    const { useSessionStore } = await import('@/stores/session')
    const { notify } = await import('@/composables/useNotifications')
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-t', makeSession({ session_id: 'sess-t', state: 'active', is_processing: true }))

    await runStateChangeSequence('sess-t', [
      { state: 'active', is_processing: false },
      { state: 'active', is_processing: false }
    ])

    const taskCompleteCalls = notify.mock.calls.filter(call => call[0] === 'task_complete')
    expect(taskCompleteCalls).toHaveLength(1)
  })
})

describe('polling store - stall-heal watchdog (#1795)', () => {
  // These tests fake only Date so checkSessionStall()'s Date.now() comparisons are
  // controllable, while setTimeout/setInterval stay real — that keeps the background
  // stall-detector interval (started by connectSession) from firing mid-test and avoids
  // needing to wait out disconnectSession()'s real 3s loop-exit budget.
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(1_700_000_000_000)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  function advanceTime(ms) {
    vi.setSystemTime(new Date(vi.getMockedSystemTime().getTime() + ms))
  }

  function flush() {
    return new Promise(resolve => setTimeout(resolve, 0))
  }

  function abortAwareFetchMock() {
    return vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => new Promise((_resolve, reject) => {
      opts?.signal?.addEventListener('abort', () => {
        const err = new Error('Aborted')
        err.name = 'AbortError'
        reject(err)
      })
    }))
  }

  async function setup(sessionOverrides) {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()
    const sid = sessionOverrides.session_id
    sessionStore.sessions.set(sid, makeSession(sessionOverrides))
    apiMock.get.mockResolvedValue({ cursor: 0 })
    return { pollingStore, sessionStore, messageStore, sid }
  }

  it('T1: heals a stalled active-processing connection once the unified threshold elapses', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-t1', is_processing: true })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    advanceTime(41000) // past the unified STALL_TIMEOUT_MS (40s) — intentional behavior change from the
    // old 15s active-processing threshold: is_processing no longer selects a faster threshold, since the
    // heartbeat measures poll round-trip freshness (bounded by the server's 30s clamp) which behaves the
    // same whether the session is active or idle.

    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('T2: heals a stalled idle connection once the unified threshold elapses', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-t2', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    advanceTime(41000) // past STALL_TIMEOUT_MS (40s) — old is_processing-gated code would never have caught this

    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('T3: does not heal a genuinely idle but healthy connection', async () => {
    const { pollingStore, sid } = await setup({ session_id: 'sess-t3', is_processing: false })

    let resolveFetch
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)

    for (let i = 0; i < 3; i++) {
      advanceTime(25000) // simulate the server holding the long-poll request open with no events
      resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: i + 1 }) })
      await flush()
      await pollingStore.checkSessionStall()
      expect(pollingStore.sessionConnected).toBe(true)
    }
    expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(4)
  })

  it('T3b: does not heal a healthy-but-quiet active-processing connection (unified threshold clears the 30s server clamp)', async () => {
    const { pollingStore, sid } = await setup({ session_id: 'sess-t3b', is_processing: true })

    let resolveFetch
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)

    for (let i = 0; i < 3; i++) {
      advanceTime(25000) // simulate the server holding the long-poll request open with no events,
      // e.g. a long-running tool call with no incremental SDK messages to relay
      resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: i + 1 }) })
      await flush()
      await pollingStore.checkSessionStall()
      expect(pollingStore.sessionConnected).toBe(true)
    }
    expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(4)
  })

  it('T4: end-to-end heal cycle re-syncs via syncMessages without a manual loadMessages reload', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-t4', is_processing: false })
    apiMock.get.mockResolvedValueOnce({ cursor: 0 }).mockResolvedValue({ cursor: 12 })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 2, hasMore: false })
    const loadSpy = vi.spyOn(messageStore, 'loadMessages')
    const fetchSpy = abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000)

    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
    expect(loadSpy).not.toHaveBeenCalled()
    expect(pollingStore.sessionConnected).toBe(true)
    const lastFetchUrl = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1][0]
    expect(lastFetchUrl).toContain('since=12')
  })
})
