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
const triggerReauthMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn().mockReturnValue(null),
  triggerReauth: triggerReauthMock
}))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  triggerReauthMock.mockReset()
})

function flush() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

// Issue #1988/#2129 stage 5b: serves each entry of `responses` (a JSON-body object, or a
// function taking (url, opts) for full control) to successive fetch() calls in order; once
// exhausted, every further call hangs until aborted, so the poll loop parks instead of
// spinning once the scripted sequence ends.
function sequencedFetchMock(responses) {
  let i = 0
  return vi.spyOn(global, 'fetch').mockImplementation((url, opts) => {
    if (i < responses.length) {
      const entry = responses[i]
      i++
      if (typeof entry === 'function') return entry(url, opts)
      return Promise.resolve({ ok: true, json: () => Promise.resolve(entry) })
    }
    return new Promise((_resolve, reject) => {
      opts?.signal?.addEventListener('abort', () => {
        const err = new Error('Aborted')
        err.name = 'AbortError'
        reject(err)
      })
    })
  })
}

// Delivers a single 'session_reset' UI-poll event for sessionId, while any concurrent
// session-poll fetch ('/api/poll/session/...') keeps hanging until aborted — used by the
// reset-during-resync dedicated test below.
async function fireSessionResetEvent(pollingStore, sessionId) {
  let uiFetchCallCount = 0
  vi.spyOn(global, 'fetch').mockImplementation((url, opts) => {
    if (String(url).includes('/api/poll/session/')) {
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    }
    uiFetchCallCount++
    if (uiFetchCallCount === 1) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          events: [{ type: 'session_reset', data: { session_id: sessionId } }],
          next_cursor: 1
        })
      })
    }
    return new Promise(() => {})
  })

  pollingStore.startUIPolling()
  await flush()
  pollingStore.stopUIPolling()
}

async function setupPollSession(sessionOverrides) {
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

  it('stopUIPolling sets uiState to idle', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    pollingStore.uiState = 'connected'
    pollingStore.stopUIPolling()

    expect(pollingStore.uiState).toBe('idle')
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

describe('polling store - backendStatus (issue #1989, stage 5b: tracked per StreamConnection)', () => {
  it('starts as ok on both streams', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    expect(pollingStore.uiBackendStatus).toBe('ok')
    expect(pollingStore.sessionBackendStatus).toBe('ok')
  })

  it('a UI-poll response carrying backend_status updates the UI stream\'s own field', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedFetchMock([
      { events: [], next_cursor: 1, backend_status: 'unreachable' },
    ])

    pollingStore.startUIPolling()
    await flush()
    // Asserted before stopUIPolling(): disconnect() now resets backendStatus to 'ok'
    // (builder-review fix — otherwise a stale unreachable/degraded value from a torn-down
    // connection could leak into whatever uses this connection next).
    expect(pollingStore.uiBackendStatus).toBe('unreachable')
    pollingStore.stopUIPolling()
  })

  it('a UI-poll response carrying backend_status: degraded updates it', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedFetchMock([
      { events: [], next_cursor: 1, backend_status: 'degraded' },
    ])

    pollingStore.startUIPolling()
    await flush()
    expect(pollingStore.uiBackendStatus).toBe('degraded')
    pollingStore.stopUIPolling()
  })

  it('a subsequent response carrying ok clears a prior unreachable/degraded state (AC4)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    // Single mutable resolver: each fetch() call gets a fresh pending promise and
    // repoints this variable at its resolve function, so successive calls can be
    // driven one at a time from the test.
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    pollingStore.startUIPolling()
    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1, backend_status: 'unreachable' }) })
    await flush()
    expect(pollingStore.uiBackendStatus).toBe('unreachable')

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 2, backend_status: 'ok' }) })
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.uiBackendStatus).toBe('ok')
  })

  it('a response with the field entirely absent defaults to ok', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedFetchMock([
      { events: [], next_cursor: 1 },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.uiBackendStatus).toBe('ok')
  })

  it('a session-poll response carrying backend_status updates the SESSION stream\'s own field, independent of the UI stream\'s', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-bs', makeSession({ session_id: 'sess-bs' }))
    apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })

    sequencedFetchMock([
      { events: [], next_cursor: 1, backend_status: 'unreachable' },
    ])

    await pollingStore.connectSession('sess-bs')
    await flush()
    // Asserted before disconnectSession(): disconnect() now resets backendStatus to 'ok'.
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    expect(pollingStore.uiBackendStatus).toBe('ok')
    await pollingStore.disconnectSession()
  })

  // Regression test (builder-review finding, carried over from pre-5b): the session-poll
  // loop's backendStatus write originally happened before the isCurrentGeneration guard —
  // a stale response landing after this session was superseded (e.g. by a session switch)
  // could clobber a fresher value written by whatever poll loop replaced it.
  it('a session-poll response for a no-longer-current session does not clobber a fresher backendStatus', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-race', makeSession({ session_id: 'sess-race' }))
    apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })

    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession('sess-race')
    pollingStore.sessionBackendStatus = 'ok'

    // Simulate this session no longer being current (e.g. a session switch) while the
    // poll request above is still in flight.
    pollingStore.currentSessionId = 'some-other-session'

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1, backend_status: 'unreachable' }) })
    await flush()

    expect(pollingStore.sessionBackendStatus).toBe('ok')
  })
})

describe('polling store - 401 re-auth (issue #2040)', () => {
  it('startUIPolling: a 401 calls triggerReauth, sets uiState idle, and does not schedule a backoff retry', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const pollingStore = usePollingStore()

      const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: 'unauthorized' })
      })

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)

      expect(triggerReauthMock).toHaveBeenCalledWith(401)
      expect(pollingStore.uiState).toBe('idle')

      const callsAfterFirst = fetchSpy.mock.calls.length
      // No backoff wait/retry: advancing well past the first backoff delay (2000ms)
      // must not produce a second fetch call, since the loop already returned.
      await vi.advanceTimersByTimeAsync(5000)
      expect(fetchSpy.mock.calls.length).toBe(callsAfterFirst)
    } finally {
      vi.useRealTimers()
    }
  })

  it('startUIPolling: a 401 goes through stopUIPolling() so the periodic app-data retry watcher is also stopped (no leaked interval)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      // A real-world confluence: app-data load had already failed before the 401 hit,
      // so the periodic watcher (started by startUIPolling()) would otherwise keep
      // retrying loadAppData() every 15s forever once the UI-poll loop exits.
      uiStore.setAppDataStatus('failed')
      apiMock.get.mockResolvedValue({})

      vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: 'unauthorized' })
      })

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)

      expect(pollingStore.uiState).toBe('idle')

      apiMock.get.mockClear()
      // Past the periodic watcher's 15s interval — if stopAppDataRetryWatcher() wasn't
      // called, this would fire retryStaleAppData() -> loadAppData() -> /api/projects
      // and /api/sessions.
      await vi.advanceTimersByTimeAsync(16000)

      expect(apiMock.get).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('_runSessionPollLoop (via connectSession): a 401 calls triggerReauth, sets sessionState idle, and stops the loop', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-401', makeSession({ session_id: 'sess-401' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.resolve({ detail: 'unauthorized' })
    })

    await pollingStore.connectSession('sess-401')
    await flush()

    expect(triggerReauthMock).toHaveBeenCalledWith(401)
    expect(pollingStore.sessionState).toBe('idle')

    const callsAfterFirst = fetchSpy.mock.calls.length
    await flush()
    // Loop must have exited (break) rather than continuing to poll.
    expect(fetchSpy.mock.calls.length).toBe(callsAfterFirst)
  })

  it('_runSessionPollLoop: existing 404 "session not found" behavior is unaffected (regression check)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-404', makeSession({ session_id: 'sess-404' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: 'not found' })
    })

    await pollingStore.connectSession('sess-404')
    await flush()

    expect(triggerReauthMock).not.toHaveBeenCalled()
    expect(pollingStore.sessionState).toBe('idle')
  })

  it('startUIPolling: a 403 still enters the existing generic catch/backoff path, unchanged (regression check)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const pollingStore = usePollingStore()

      vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: false,
        status: 403,
        json: () => Promise.resolve({ detail: 'forbidden' })
      })

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)

      expect(triggerReauthMock).not.toHaveBeenCalled()
      expect(pollingStore.uiState).toBe('backoff')
      expect(pollingStore.uiRetryCount).toBe(1)

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('startUIPolling: a 500 still enters the existing generic catch/backoff path, unchanged (regression check)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const pollingStore = usePollingStore()

      vi.spyOn(global, 'fetch').mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: 'server error' })
      })

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)

      expect(triggerReauthMock).not.toHaveBeenCalled()
      expect(pollingStore.uiState).toBe('backoff')
      expect(pollingStore.uiRetryCount).toBe(1)

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })
})

// Issue #1977: mocks /api/projects and /api/sessions distinctly so fetchProjects()/
// fetchSessions() (both called by loadAppData()) each get a shape they can parse
// without throwing on `.forEach` of an undefined array.
function mockAppDataEndpoints({ projectsOk = true, sessionsOk = true } = {}) {
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/projects') {
      return projectsOk ? Promise.resolve({ projects: [] }) : Promise.reject(new Error('projects fetch failed'))
    }
    if (url === '/api/sessions') {
      return sessionsOk ? Promise.resolve({ sessions: [] }) : Promise.reject(new Error('sessions fetch failed'))
    }
    return Promise.resolve({})
  })
}

// A fetch mock for startUIPolling()'s own transport: the first call rejects (driving the
// catch → backoff → reconnect path under test), every call after that hangs until aborted
// (mirrors abortAwareFetchMock above) so the loop doesn't spin through repeated real errors
// once stopUIPolling() is called at the end of each test.
function rejectOnceThenHangFetchMock() {
  let callCount = 0
  return vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
    callCount++
    if (callCount === 1) {
      return Promise.reject(new Error('Connection refused'))
    }
    return new Promise((_resolve, reject) => {
      opts?.signal?.addEventListener('abort', () => {
        const err = new Error('Aborted')
        err.name = 'AbortError'
        reject(err)
      })
    })
  })
}

// Like rejectOnceThenHangFetchMock(), but the second call succeeds with an empty poll
// response — this is what actually confirms a reconnect (retryStaleAppData() only fires
// on a CONFIRMED successful poll, not merely once the backoff delay elapses; see #1977).
// Every call after the second hangs until aborted, same as above.
function rejectOnceThenSucceedThenHangFetchMock() {
  let callCount = 0
  return vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
    callCount++
    if (callCount === 1) {
      return Promise.reject(new Error('Connection refused'))
    }
    if (callCount === 2) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ events: [], next_cursor: 1 }),
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
}

describe('polling store - loadAppData / appDataStatus (issue #1977)', () => {
  it('loadAppData sets appDataStatus to failed when either fetch rejects', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints({ projectsOk: false })

    await pollingStore.loadAppData()

    expect(uiStore.appDataStatus).toBe('failed')
  })

  it('loadAppData sets appDataStatus to loaded when both fetches succeed', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints()

    await pollingStore.loadAppData()

    expect(uiStore.appDataStatus).toBe('loaded')
  })

  it('overlapping loadAppData calls collapse into a single in-flight load (T3)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    let resolveProjects
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/projects') return new Promise(resolve => { resolveProjects = resolve })
      if (url === '/api/sessions') return Promise.resolve({ sessions: [] })
      return Promise.resolve({})
    })

    const first = pollingStore.loadAppData()
    pollingStore.loadAppData()

    // One call each for /api/projects and /api/sessions — the second loadAppData()
    // call must not have triggered a second pair of fetches (Pinia's action wrapper
    // means the two calls don't return the identical Promise instance, so the in-flight
    // guard is verified here via call count instead of reference equality).
    expect(apiMock.get).toHaveBeenCalledTimes(2)

    resolveProjects({ projects: [] })
    await first
  })

  it('reconnect after backoff retries loadAppData when appDataStatus is not loaded (AC7)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      uiStore.setAppDataStatus('failed')
      mockAppDataEndpoints()
      rejectOnceThenSucceedThenHangFetchMock()

      pollingStore.startUIPolling()

      // Flush the rejected first poll fetch through the catch block, then advance past
      // the first backoff delay (2000ms * 1 retry) to the reconnect point.
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      // Let the confirming second poll fetch (a real, unfaked promise chain) resolve,
      // then the reconnect-triggered loadAppData() it fires settle.
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(0)

      expect(uiStore.appDataStatus).toBe('loaded')

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not double-fetch on the normal happy-path first connection (appDataStatus already loaded)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      // Happy path: initial load already resolved appDataStatus before startUIPolling()
      // was ever called (as App.vue's initializeApp() does).
      uiStore.setAppDataStatus('loaded')
      mockAppDataEndpoints()
      rejectOnceThenHangFetchMock()

      pollingStore.startUIPolling()

      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)

      // loadAppData() would call /api/projects and /api/sessions — neither should have
      // been hit by the reconnect, since appDataStatus was already 'loaded'.
      expect(apiMock.get).not.toHaveBeenCalledWith('/api/projects')
      expect(apiMock.get).not.toHaveBeenCalledWith('/api/sessions')

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not re-fire the retry on every backoff tick during a sustained total outage', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      uiStore.setAppDataStatus('failed')
      mockAppDataEndpoints()
      // Every /api/poll/ui call fails — a sustained total outage, not a one-off blip.
      vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Connection refused'))

      pollingStore.startUIPolling()

      // Three backoff cycles (2000ms, 4000ms, 6000ms) — each re-enters 'connecting'
      // optimistically, but since /api/poll/ui never actually succeeds, retryStaleAppData()
      // (and its two REST calls) must never fire during any of them.
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(4000)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(6000)
      await vi.advanceTimersByTimeAsync(0)

      expect(apiMock.get).not.toHaveBeenCalledWith('/api/projects')
      expect(apiMock.get).not.toHaveBeenCalledWith('/api/sessions')
      expect(uiStore.appDataStatus).toBe('failed')

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('periodic watcher retries a failed app-data load even when /api/poll/ui itself never errors (AC7)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      // /api/projects failed on the initial load, but the UI-poll transport itself is
      // healthy throughout (a real, independent failure mode) — the reconnect-triggered
      // retry inside startUIPolling()'s catch block never fires in this case, so recovery
      // depends entirely on the periodic watcher.
      uiStore.setAppDataStatus('failed')
      mockAppDataEndpoints()
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      }))

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)

      // Before the watcher's first tick, still failed.
      expect(uiStore.appDataStatus).toBe('failed')

      await vi.advanceTimersByTimeAsync(15000)

      expect(uiStore.appDataStatus).toBe('loaded')

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('stopUIPolling stops the periodic app-data retry watcher', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      uiStore.setAppDataStatus('failed')
      // Always failing — if the watcher kept running after stop, appDataStatus would
      // still flip through 'loading' on every tick, which we can detect below.
      apiMock.get.mockRejectedValue(new Error('still down'))
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      }))

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)
      pollingStore.stopUIPolling()

      apiMock.get.mockClear()
      await vi.advanceTimersByTimeAsync(30000)

      expect(apiMock.get).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('reconnect also retries a transient deepLinkFailure alongside loadAppData (partial T4)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const { useSessionStore } = await import('@/stores/session')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()
      const sessionStore = useSessionStore()

      uiStore.setAppDataStatus('failed')
      uiStore.setDeepLinkFailure({ sessionId: 'sess-deep', kind: 'transient', message: 'oops' })
      // The user is still on the session the deep-link fetch failed for (selectSession()
      // sets currentSessionId optimistically even on failure) — this is the condition the
      // retry is gated on, so it must be set for the retry to actually fire.
      sessionStore.currentSessionId = 'sess-deep'
      mockAppDataEndpoints()
      rejectOnceThenSucceedThenHangFetchMock()

      const selectSpy = vi.spyOn(sessionStore, 'selectSession').mockResolvedValue(undefined)

      pollingStore.startUIPolling()

      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(0)

      expect(selectSpy).toHaveBeenCalledWith('sess-deep')
      // Bypasses selectSession()'s early-return guard (currentSessionId already equals
      // sessionId with no selection in flight) — without this reset the retry is a no-op.
      expect(sessionStore.currentSessionId).toBeNull()

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('reconnect does not retry a transient deepLinkFailure for a session the user has since navigated away from', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const { useSessionStore } = await import('@/stores/session')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()
      const sessionStore = useSessionStore()

      uiStore.setAppDataStatus('failed')
      uiStore.setDeepLinkFailure({ sessionId: 'sess-deep', kind: 'transient', message: 'oops' })
      // The user navigated to a different session after the deep-link failure — the stale
      // failure must not hijack them back to 'sess-deep'.
      sessionStore.currentSessionId = 'sess-other'
      mockAppDataEndpoints()
      rejectOnceThenSucceedThenHangFetchMock()

      const selectSpy = vi.spyOn(sessionStore, 'selectSession').mockResolvedValue(undefined)

      pollingStore.startUIPolling()

      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(0)

      expect(selectSpy).not.toHaveBeenCalled()
      expect(sessionStore.currentSessionId).toBe('sess-other')

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('reconnect does not retry a permanent (not-found) deepLinkFailure', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const { useSessionStore } = await import('@/stores/session')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()
      const sessionStore = useSessionStore()

      uiStore.setAppDataStatus('failed')
      uiStore.setDeepLinkFailure({ sessionId: 'sess-gone', kind: 'not-found', message: 'gone' })
      mockAppDataEndpoints()
      rejectOnceThenSucceedThenHangFetchMock()

      const selectSpy = vi.spyOn(sessionStore, 'selectSession').mockResolvedValue(undefined)

      pollingStore.startUIPolling()

      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(0)

      expect(selectSpy).not.toHaveBeenCalled()

      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('polling store - UI-stream evicted/reset consistency (issue #2065 AC7)', () => {
  it('review finding: a session that vanishes during the evicted/reset resync still gets the full session_deleted cleanup, not just removal from the sessions Map', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()

    const sid = 'sess-vanished'
    sessionStore.sessions.set(sid, makeSession({ session_id: sid }))
    sessionStore.currentSessionId = sid
    messageStore.messagesBySession.set(sid, [{ id: 'm1', type: 'user' }])

    // mockAppDataEndpoints()'s /api/sessions mock returns an empty list — the seeded
    // session "vanishes" from the resync's own ground truth, exactly like a real
    // session_deleted that landed in the same evicted/reset cycle would.
    mockAppDataEndpoints()

    sequencedFetchMock([
      {
        // Epic #1990 stage 5a: reset is now derived from "batch contains a
        // stream_reset event" — a real backend never sends reset=true without one.
        events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }],
        next_cursor: 5,
        reset: true,
      },
    ])

    pollingStore.startUIPolling()
    // More awaited steps than the other evicted/reset tests — disconnectSession() plus
    // removeSessionsFromStores()'s own chain of per-store dynamic imports — and dynamic
    // import() resolution under real timers doesn't reliably settle within a fixed count
    // of flush() ticks, so poll for completion instead of counting ticks.
    for (let i = 0; i < 20; i++) {
      if (!messageStore.messagesBySession.has(sid)) break
      await flush()
    }
    pollingStore.stopUIPolling()

    expect(sessionStore.sessions.has(sid)).toBe(false)
    expect(messageStore.messagesBySession.has(sid)).toBe(false)
    expect(sessionStore.currentSessionId).toBeNull()
  })

  it('reset=true triggers loadAppData() and does not dispatch the carried events', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints()
    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedFetchMock([
      {
        // Epic #1990 stage 5a: a carried event AFTER the stream_reset marker in the
        // same batch — must be dropped in favor of the fallback resync, not
        // dispatched as if it were a trustworthy incremental update.
        events: [
          { type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } },
          { type: 'rate_limits_update', data: { requests_remaining: 999 } },
        ],
        next_cursor: 5,
        reset: true,
      },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(apiMock.get).toHaveBeenCalledWith('/api/projects')
    expect(apiMock.get).toHaveBeenCalledWith('/api/sessions')
    expect(setRateLimitsSpy).not.toHaveBeenCalledWith({ requests_remaining: 999 })
  })

  it('mid-batch stream_reset: events before the marker are dispatched normally, the marker and anything after trigger the resync (epic #1990 stage 5a)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints()
    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedFetchMock([
      {
        // A pre-marker event is legitimate pre-reset history and needs no
        // unwinding; a post-marker event in the SAME batch is superseded by
        // the resync and must not be applied as a trustworthy incremental update.
        events: [
          { type: 'rate_limits_update', data: { requests_remaining: 111 } },
          { type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } },
          { type: 'rate_limits_update', data: { requests_remaining: 999 } },
        ],
        next_cursor: 5,
        reset: true,
      },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(setRateLimitsSpy).toHaveBeenCalledWith({ requests_remaining: 111 })
    expect(setRateLimitsSpy).not.toHaveBeenCalledWith({ requests_remaining: 999 })
    expect(apiMock.get).toHaveBeenCalledWith('/api/projects')
    expect(apiMock.get).toHaveBeenCalledWith('/api/sessions')
  })

  it('evicted=true triggers loadAppData() and does not dispatch the carried events', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints()
    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedFetchMock([
      {
        events: [{ type: 'rate_limits_update', data: { requests_remaining: 999 } }],
        next_cursor: 5,
        evicted: true,
      },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(apiMock.get).toHaveBeenCalledWith('/api/projects')
    expect(apiMock.get).toHaveBeenCalledWith('/api/sessions')
    expect(setRateLimitsSpy).not.toHaveBeenCalledWith({ requests_remaining: 999 })
  })

  it('a normal response with no evicted/reset still dispatches its events as before', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedFetchMock([
      { events: [{ type: 'rate_limits_update', data: { requests_remaining: 42 } }], next_cursor: 1 },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(setRateLimitsSpy).toHaveBeenCalledWith({ requests_remaining: 42 })
    // No app-data resync should have been triggered for a plain, non-evicted/reset cycle.
    expect(apiMock.get).not.toHaveBeenCalledWith('/api/projects')
  })
})

// ============================================================================
// Epic #1990 stage 5b: StreamConnection state machine replaces the stall/heal
// heuristic layer entirely. The describe blocks below replace the deleted
// stall-heal watchdog (#1795), freeze-forgiveness watchdog (#1974), and
// cursor-atomicity (#1988) suites — liveness is now derived only from poll
// responses/fetch outcomes, so there's no wall-clock heuristic left to test.
// ============================================================================

describe('polling store - StreamConnection transition table (T1, issue #2129 stage 5b)', () => {
  it('idle -> connect(cursor) -> connecting, immediately on connectSession()', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-connecting' })
    // Never resolves — only the synchronous state right after connect() matters here.
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => new Promise((_resolve, reject) => {
      opts?.signal?.addEventListener('abort', () => {
        const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
      })
    }))

    await pollingStore.connectSession(sid)

    expect(pollingStore.sessionState).toBe('connecting')
    expect(pollingStore.sessionRetryCount).toBe(0)
    await pollingStore.disconnectSession()
  })

  it('connecting + poll OK normal batch -> connected, retryCount resets to 0', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-connected' })
    sequencedFetchMock([{ events: [], next_cursor: 1 }])

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionRetryCount).toBe(0)
    expect(pollingStore.sessionHasEverConnected).toBe(true)
    await pollingStore.disconnectSession()
  })

  it('connected + poll OK but backend_status unreachable -> unreachable (AC2: backend_status decides it, literally)', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-unreachable' })
    // Manual single-resolver pacing (same pattern as the kept backendStatus tests above) —
    // sequencedFetchMock's immediately-resolving entries would otherwise all be consumed
    // within one flush(), since nothing forces a macrotask boundary between loop iterations.
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)
    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1 }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 2, backend_status: 'unreachable' }) })
    await flush()

    expect(pollingStore.sessionState).toBe('unreachable')
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    await pollingStore.disconnectSession()
  })

  it('connected + poll OK with backend_status degraded stays connected (decision 1: degraded is a non-overriding annotation, not a state)', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-degraded' })
    sequencedFetchMock([{ events: [], next_cursor: 1, backend_status: 'degraded' }])

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionBackendStatus).toBe('degraded')
    await pollingStore.disconnectSession()
  })

  it('connected + poll OK, batch contains stream_reset -> resyncing, then connected once the resync resolves', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-t1-resync' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid
    let resolveLoad
    vi.spyOn(messageStore, 'loadMessages').mockImplementation(() => new Promise(resolve => { resolveLoad = resolve }))

    sequencedFetchMock([
      { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
    ])

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('resyncing')

    resolveLoad()
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    await pollingStore.disconnectSession()
  })

  it('connecting/connected/resyncing/unreachable + fetch throws (non-abort) -> backoff, retryCount++', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-backoff' })
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Connection refused'))

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('backoff')
    expect(pollingStore.sessionRetryCount).toBe(1)
    await pollingStore.disconnectSession()
  })

  it('backoff + timer fires -> connecting (retry fetch)', async () => {
    vi.useFakeTimers()
    try {
      const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-backoff-retry' })
      let callCount = 0
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
        callCount++
        if (callCount === 1) return Promise.reject(new Error('Connection refused'))
        return new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () => {
            const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
          })
        })
      })

      await pollingStore.connectSession(sid)
      await vi.advanceTimersByTimeAsync(0)
      expect(pollingStore.sessionState).toBe('backoff')

      await vi.advanceTimersByTimeAsync(2000)
      expect(pollingStore.sessionState).toBe('connecting')
      expect(callCount).toBe(2)

      await pollingStore.disconnectSession()
    } finally {
      vi.useRealTimers()
    }
  })

  it('connecting + fetch aborted (visibility resume) -> stays connecting, retries immediately, no retryCount bump', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-abort-resume' })
    let callCount = 0
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
      callCount++
      if (callCount === 1) {
        const err = new Error('Aborted'); err.name = 'AbortError'
        return Promise.reject(err)
      }
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
        })
      })
    })

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('connecting')
    expect(pollingStore.sessionRetryCount).toBe(0)
    expect(callCount).toBe(2)
    await pollingStore.disconnectSession()
  })

  it('resyncing + resync (loadMessages) throws -> backoff, same path as any other fetch error (no second retry mechanism)', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-t1-resync-fail' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid
    vi.spyOn(messageStore, 'loadMessages').mockRejectedValue(new Error('history fetch failed'))

    sequencedFetchMock([
      { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(pollingStore.sessionState).toBe('backoff')
    expect(pollingStore.sessionRetryCount).toBe(1)
    await pollingStore.disconnectSession()
  })

  it('any non-idle/non-archived + disconnect() -> idle (abort, clear backoff timer, bump generation, retryCount=0)', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-disconnect' })
    sequencedFetchMock([{ events: [], next_cursor: 1 }])

    await pollingStore.connectSession(sid)
    await flush()
    expect(pollingStore.sessionState).toBe('connected')

    await pollingStore.disconnectSession()

    expect(pollingStore.sessionState).toBe('idle')
    expect(pollingStore.sessionRetryCount).toBe(0)
    expect(pollingStore.currentSessionId).toBeNull()
  })

  it('disconnect() during an active backoff sleep wakes the loop immediately instead of waiting out the full delay', async () => {
    vi.useFakeTimers()
    try {
      const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-disconnect-backoff' })
      vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Connection refused'))

      await pollingStore.connectSession(sid)
      await vi.advanceTimersByTimeAsync(0)
      expect(pollingStore.sessionState).toBe('backoff')

      await pollingStore.disconnectSession()
      expect(pollingStore.sessionState).toBe('idle')

      // If the backoff sleep weren't cancelled, advancing past its 2000ms delay would
      // still be a no-op here (generation already stale) — the real assertion is that
      // disconnectSession() itself resolved promptly above, not hung on the old timer.
      await vi.advanceTimersByTimeAsync(2000)
      expect(pollingStore.sessionState).toBe('idle')
    } finally {
      vi.useRealTimers()
    }
  })

  it('UI stream: connecting + poll OK -> connected; batch with stream_reset -> resyncing -> connected', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()
    mockAppDataEndpoints()

    let resolveLoad
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore()
    vi.spyOn(sessionStore, 'fetchSessions').mockImplementation(() => new Promise(resolve => { resolveLoad = resolve }))

    // Manual single-resolver pacing — see the identical note on the session-side test
    // above; an immediately-resolving sequencedFetchMock entry would race straight past
    // the intermediate 'connected' observation within the same microtask flush.
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    pollingStore.startUIPolling()
    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1 }) })
    await flush()
    expect(pollingStore.uiState).toBe('connected')

    resolveFetch({
      ok: true,
      json: () => Promise.resolve({
        events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }],
        next_cursor: 5,
      })
    })
    await flush()
    expect(pollingStore.uiState).toBe('resyncing')

    resolveLoad([])
    await flush()
    expect(pollingStore.uiState).toBe('connected')

    pollingStore.stopUIPolling()
  })
})

// T2 (plan's Testing Strategy): proves the ABSENCE of any wall-clock staleness mechanism —
// a frozen tab (JS timers simply not firing for a long stretch) must not trigger any
// reconnect storm or state change on its own, since nothing in the new design is driven by
// elapsed wall-clock time at all. This test exists to prove that absence, not to exercise
// forgiveness math (there is none left).
describe('polling store - frozen tab, no wall-clock timers (T2, issue #2129 stage 5b)', () => {
  it('a long-pending in-flight poll fetch produces no reconnect storm and loses no events once it finally resolves', async () => {
    vi.useFakeTimers()
    try {
      const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t2-frozen' })
      let resolveFetch
      let fetchCallCount = 0
      vi.spyOn(global, 'fetch').mockImplementation(() => {
        fetchCallCount++
        return new Promise(resolve => { resolveFetch = resolve })
      })

      await pollingStore.connectSession(sid)
      expect(fetchCallCount).toBe(1)

      // Simulate a tab frozen for 10 minutes — nothing in the new design schedules any
      // timer off this poll's wall-clock duration, so advancing fake time here must not
      // provoke a second fetch call or any state change at all.
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(fetchCallCount).toBe(1)
      expect(pollingStore.sessionState).toBe('connecting')

      // The tab "wakes up" and the long-held poll finally resolves normally.
      resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 7 }) })
      await vi.advanceTimersByTimeAsync(0)

      expect(pollingStore.sessionState).toBe('connected')
      expect(pollingStore.sessionRetryCount).toBe(0)

      await pollingStore.disconnectSession()
    } finally {
      vi.useRealTimers()
    }
  })
})

// T3 (plan's Testing Strategy): backend_status ok -> unreachable -> ok across successive
// poll responses, asserting the state/backendStatus sequence tracks exactly as specified.
// InputArea's own consumption of this sequence (isConnected/isBackendBlocking) is covered
// directly in InputArea.test.js's "backend outage gating" describe block — this test
// covers the STORE side of the same sequence.
describe('polling store - backend_status ok -> unreachable -> ok scenario (T3, issue #2129 stage 5b)', () => {
  it('session connection tracks the full ok -> unreachable -> ok sequence correctly', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t3' })
    // Manual single-resolver pacing — see the note on the T1 "unreachable" test above.
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)
    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1, backend_status: 'ok' }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionBackendStatus).toBe('ok')

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 2, backend_status: 'unreachable' }) })
    await flush()
    expect(pollingStore.sessionState).toBe('unreachable')
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    // sessionHasEverConnected stays true throughout — InputArea's gate (decision 4) must
    // not flip back to "never connected" just because the backend self-heals later.
    expect(pollingStore.sessionHasEverConnected).toBe(true)

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 3, backend_status: 'ok' }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionBackendStatus).toBe('ok')

    await pollingStore.disconnectSession()
  })
})

// Dedicated risk test (plan's Risks & Considerations): the evicted/reset cursor-adoption
// handoff via loadedEventCursors must transfer byte-for-byte into the new design — this is
// called out as the highest-value-to-regress path in the whole rewrite.
describe('polling store - dedicated risk: evicted/reset resync cursor fidelity (issue #2129 stage 5b)', () => {
  it('adopts the cursor from loadedEventCursors (not next_cursor) once the resync completes, and clears the map entry', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-risk-cursor' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid

    vi.spyOn(messageStore, 'loadMessages').mockImplementation(async (sessionId) => {
      messageStore.loadedEventCursors.set(sessionId, 999)
    })

    const fetchMock = sequencedFetchMock([
      { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
      { events: [], next_cursor: 6 },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(messageStore.loadedEventCursors.has(sid)).toBe(false)
    // The poll cycle issued immediately after the resync completes proves which cursor was
    // actually adopted — 999 from loadedEventCursors, not 5 (the evicted response's own
    // next_cursor). Call index 1: call 0 is the initial evicted/reset response itself, both
    // of which the loop races through within the same microtask flush (no real network
    // delay in this mock), so a THIRD call (index 2, using the second response's own
    // next_cursor=6) also lands before disconnectSession() runs — index 1 is the one that
    // actually reflects the resync's cursor adoption.
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(fetchMock.mock.calls[1][0]).toContain('since=999')

    await pollingStore.disconnectSession()
  })

  it('falls back to next_cursor when loadMessages() does not set loadedEventCursors', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-risk-cursor-fallback' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid
    vi.spyOn(messageStore, 'loadMessages').mockResolvedValue(undefined)

    const fetchMock = sequencedFetchMock([
      { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
      { events: [], next_cursor: 6 },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(fetchMock.mock.calls[1][0]).toContain('since=5')

    await pollingStore.disconnectSession()
  })

  it('an evicted session that vanishes from the UI resync still gets full session_deleted cleanup (carried over from #2065 AC7)', async () => {
    // Same guarantee as the kept UI-stream evicted/reset consistency tests above, exercised
    // once more here as part of the dedicated risk coverage this stage explicitly calls out.
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()

    const sid = 'sess-risk-vanish'
    sessionStore.sessions.set(sid, makeSession({ session_id: sid }))
    sessionStore.currentSessionId = sid
    messageStore.messagesBySession.set(sid, [{ id: 'm1', type: 'user' }])
    mockAppDataEndpoints()

    sequencedFetchMock([
      { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
    ])

    pollingStore.startUIPolling()
    for (let i = 0; i < 20; i++) {
      if (!messageStore.messagesBySession.has(sid)) break
      await flush()
    }
    pollingStore.stopUIPolling()

    expect(sessionStore.sessions.has(sid)).toBe(false)
    expect(messageStore.messagesBySession.has(sid)).toBe(false)
  })
})

// Dedicated risk test (plan's Risks & Considerations): a session_reset UI broadcast arriving
// while a resync is already in flight for the SAME session must not corrupt state or crash —
// there's no heal-mutex left to (coincidentally) protect any part of this anymore.
describe('polling store - dedicated risk: session_reset arriving mid-resync (issue #2129 stage 5b)', () => {
  it('a session_reset for the session currently mid-resync clears the cursor cache without disturbing the in-flight resync, which still completes correctly', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-risk-reset-mid-resync' })
    const { useMessageStore } = await import('@/stores/message')
    const { useResourceStore } = await import('@/stores/resource')
    const messageStore = useMessageStore()
    const resourceStore = useResourceStore()
    sessionStore.currentSessionId = sid

    let resolveLoad
    vi.spyOn(messageStore, 'loadMessages').mockImplementation((sessionId) => new Promise(resolve => {
      resolveLoad = () => {
        messageStore.loadedEventCursors.set(sessionId, 42)
        resolve()
      }
    }))
    const clearMessagesSpy = vi.spyOn(messageStore, 'clearMessages')
    const clearResourcesSpy = vi.spyOn(resourceStore, 'clearResources')

    const sessionFetchMock = vi.spyOn(global, 'fetch').mockImplementation((url, opts) => {
      if (String(url).includes('/api/poll/session/')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }],
            next_cursor: 5,
          })
        })
      }
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
        })
      })
    })

    await pollingStore.connectSession(sid)
    await flush()
    expect(pollingStore.sessionState).toBe('resyncing')

    // The session_reset UI broadcast for this exact session arrives WHILE the resync's
    // loadMessages() is still pending. UI-poll calls after the first must hang (not keep
    // re-resolving the same event), or the UI loop spins on it forever.
    let uiCallCount = 0
    sessionFetchMock.mockImplementation((url, opts) => {
      if (String(url).includes('/api/poll/session/')) {
        return new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () => {
            const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
          })
        })
      }
      uiCallCount++
      if (uiCallCount === 1) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            events: [{ type: 'session_reset', data: { session_id: sid } }],
            next_cursor: 1,
          })
        })
      }
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
        })
      })
    })
    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(clearMessagesSpy).toHaveBeenCalledWith(sid)
    expect(clearResourcesSpy).toHaveBeenCalledWith(sid)
    // Still resyncing — the UI-side session_reset handler doesn't touch the session
    // connection's own in-flight resync at all (separate, independent state).
    expect(pollingStore.sessionState).toBe('resyncing')

    // Now the in-flight resync itself resolves — it must complete correctly despite the
    // concurrent reset, adopting its own cursor and reaching 'connected'.
    resolveLoad()
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    await pollingStore.disconnectSession()
  })
})

// Dedicated risk test (plan's Risks & Considerations): the old awaited 3-second teardown
// budget is replaced by a synchronous generation-guarded teardown — switching sessions while
// a fetch is in flight for the old session must not let its stale response corrupt the new
// session's state.
describe('polling store - dedicated risk: session-switch-mid-fetch race (issue #2129 stage 5b)', () => {
  it('a stale in-flight response for the OLD session cannot clobber the NEW session\'s state after a mid-fetch switch', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-old', makeSession({ session_id: 'sess-old' }))
    sessionStore.sessions.set('sess-new', makeSession({ session_id: 'sess-new' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    let resolveOldFetch
    let fetchCallCount = 0
    vi.spyOn(global, 'fetch').mockImplementation((url) => {
      fetchCallCount++
      if (fetchCallCount === 1) {
        // The OLD session's poll — left pending so the switch below races it.
        return new Promise(resolve => { resolveOldFetch = resolve })
      }
      // The NEW session's poll(s) — hang indefinitely, same as abortAwareFetchMock.
      return new Promise(() => {})
    })

    await pollingStore.connectSession('sess-old')
    expect(fetchCallCount).toBe(1)

    // Switch to a different session WHILE the old session's first fetch is still pending —
    // disconnectSession() (called internally by connectSession()) is now fully synchronous,
    // no 3-second exit budget to await.
    await pollingStore.connectSession('sess-new')

    expect(pollingStore.currentSessionId).toBe('sess-new')
    expect(pollingStore.sessionState).toBe('connecting')

    // The old session's stale response finally arrives — it must be a complete no-op.
    resolveOldFetch({
      ok: true,
      json: () => Promise.resolve({ events: [], next_cursor: 999, backend_status: 'unreachable' })
    })
    await flush()

    expect(pollingStore.currentSessionId).toBe('sess-new')
    // Neither the state nor backendStatus were clobbered by the stale sess-old response.
    expect(pollingStore.sessionState).toBe('connecting')
    expect(pollingStore.sessionBackendStatus).toBe('ok')

    await pollingStore.disconnectSession()
  })
})

// Explicit test for the gap WebUI-Agent confirmed should stay load-bearing: sessionCursors
// is a cross-session cursor CACHE (not heuristic bookkeeping), separate from each
// StreamConnection's own private live-cursor field — switching away and back must resume
// from exactly where the session left off, not silently skip the gap period.
describe('polling store - cross-session cursor cache (issue #1974/#2129 stage 5b)', () => {
  it('switching A -> B -> back to A resumes A from its cached cursor, not a fresh "cursor right now" bootstrap', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()

    sessionStore.sessions.set('sess-a', makeSession({ session_id: 'sess-a' }))
    sessionStore.sessions.set('sess-b', makeSession({ session_id: 'sess-b' }))
    // A's history is already cached from a prior visit — loadedEventCursors has nothing for
    // it (that map is only populated by a fresh loadMessages() call, which switch-back must
    // NOT trigger here), forcing connectSession() to fall back to sessionCursors.
    messageStore.messagesBySession.set('sess-a', [{ id: 'm1', type: 'user' }])
    apiMock.get.mockResolvedValue({ cursor: 0 }) // only hit for a genuinely first-time connect

    // Each phase gets its OWN single-entry mock (consumed, then torn down, before the next
    // phase begins) — a SHARED multi-entry sequence would let session A's own loop race
    // straight through B's entry too, within the same microtask flush, before
    // disconnectSession() ever gets a chance to run.
    sequencedFetchMock([{ events: [], next_cursor: 123 }])  // A's first poll cycle
    await pollingStore.connectSession('sess-a')
    await flush()
    await pollingStore.disconnectSession()

    sequencedFetchMock([{ events: [], next_cursor: 1 }])  // B's first poll cycle
    await pollingStore.connectSession('sess-b')
    await flush()
    await pollingStore.disconnectSession()

    apiMock.get.mockClear()
    const fetchMock = vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(() => {}))

    await pollingStore.connectSession('sess-a')

    // Resumed from A's cached cursor (123) — NOT a fresh bootstrap GET (which would only
    // happen if the cache had been lost) and not from cursor 0.
    expect(apiMock.get).not.toHaveBeenCalledWith('/api/poll/session/sess-a/cursor')
    const lastFetchUrl = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]
    expect(lastFetchUrl).toContain('since=123')

    await pollingStore.disconnectSession()
  })

  it('resetSessionCursor() clears only the named session\'s cache entry, forcing its next connect to start fresh', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-reset', makeSession({ session_id: 'sess-reset' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    const fetchMock = sequencedFetchMock([{ events: [], next_cursor: 55 }])

    await pollingStore.connectSession('sess-reset')
    await flush()
    await pollingStore.disconnectSession()
    pollingStore.resetSessionCursor('sess-reset')

    apiMock.get.mockClear()
    fetchMock.mockImplementation(() => new Promise(() => {}))

    await pollingStore.connectSession('sess-reset')

    // No cache left — falls all the way back to the API bootstrap, cursor 0.
    expect(apiMock.get).toHaveBeenCalledWith('/api/poll/session/sess-reset/cursor')
    const lastFetchUrl = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]
    expect(lastFetchUrl).toContain('since=0')

    await pollingStore.disconnectSession()
  })

  it('cleanupSessionPollingState() clears a deleted (non-current) session\'s cache entry too', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-cleanup', makeSession({ session_id: 'sess-cleanup' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    const fetchMock = sequencedFetchMock([{ events: [], next_cursor: 77 }])
    await pollingStore.connectSession('sess-cleanup')
    await flush()
    await pollingStore.disconnectSession()

    pollingStore.cleanupSessionPollingState('sess-cleanup')

    apiMock.get.mockClear()
    fetchMock.mockImplementation(() => new Promise(() => {}))
    await pollingStore.connectSession('sess-cleanup')

    expect(apiMock.get).toHaveBeenCalledWith('/api/poll/session/sess-cleanup/cursor')
    await pollingStore.disconnectSession()
  })
})

// AC3/UC3, decision 5: an archive/no-live-stream session view marks the session connection
// explicitly archived instead of leaving it an implicit, uninspectable absence.
describe('polling store - markSessionArchived (issue #2129 stage 5b, AC3/AC4)', () => {
  it('sets currentSessionId and sessionState to archived without starting any poll loop', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()
    const fetchSpy = vi.spyOn(global, 'fetch')

    pollingStore.markSessionArchived('sess-archived-1')

    expect(pollingStore.currentSessionId).toBe('sess-archived-1')
    expect(pollingStore.sessionState).toBe('archived')
    await flush()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('a subsequent connectSession() for a different session cleanly supersedes the archived marker', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-live', makeSession({ session_id: 'sess-live' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })
    sequencedFetchMock([{ events: [], next_cursor: 1 }])

    pollingStore.markSessionArchived('sess-archived-2')
    expect(pollingStore.sessionState).toBe('archived')

    await pollingStore.connectSession('sess-live')
    await flush()

    expect(pollingStore.currentSessionId).toBe('sess-live')
    expect(pollingStore.sessionState).toBe('connected')

    await pollingStore.disconnectSession()
  })

  // Builder-review finding: marking the SAME session's own connection archived (e.g. the
  // user opens /session/X/archive/Y for the session X they're already actively viewing —
  // a real scenario, since a live session and its archive can be open as separate
  // KeepAlive-cached instances) must stop the still-running live loop, not just overwrite
  // currentSessionId/state once and leave the old generation free to self-correct it right
  // back to 'connected' on its very next successful poll.
  it('marking the currently-connected session itself archived stops its still-running poll loop instead of letting it revert the state', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-archive-self' })
    sessionStore.currentSessionId = sid

    let resolveFetch
    let fetchCallCount = 0
    vi.spyOn(global, 'fetch').mockImplementation(() => {
      fetchCallCount++
      return new Promise(resolve => { resolveFetch = resolve })
    })

    await pollingStore.connectSession(sid)
    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1 }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')
    // The loop already raced through to its SECOND poll within this same flush (no real
    // network delay in this mock) — classic long-poll pattern: the next request is already
    // in flight by the time the user opens the archive view for this same session.
    // `resolveFetch` now points at this second, about-to-be-superseded call's resolver.
    expect(fetchCallCount).toBe(2)
    const staleResolve = resolveFetch

    pollingStore.markSessionArchived(sid)
    expect(pollingStore.sessionState).toBe('archived')

    // The old (now-superseded) generation's in-flight fetch finally resolves — without the
    // disconnect-first fix, this would silently flip the state straight back to 'connected'.
    staleResolve({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 2 }) })
    await flush()

    expect(pollingStore.sessionState).toBe('archived')
    expect(pollingStore.currentSessionId).toBe(sid)
  })
})
