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

// Shared by the stall-heal watchdog describe blocks below (#1795 and #1974) — both fake
// only Date (via each block's own beforeEach/afterEach) so checkSessionStall()'s
// Date.now() comparisons are controllable, while setTimeout/setInterval stay real.
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

// Issue #1988: serves each entry of `responses` (a JSON-body object, or a function taking
// (url, opts) for full control) to successive fetch() calls in order; once exhausted, every
// further call hangs until aborted (same behavior as abortAwareFetchMock) so the poll loop
// parks instead of spinning once the scripted sequence ends.
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

// Issue #1988: the heal body no longer has a syncMessages()/cursor-GET step whose mock call
// count can stand in for "a heal actually ran past the mutex/cooldown gates" — the new heal
// body logs `[stall-heal] Session ${sid} stalled...` exactly once per attempt that gets past
// those gates (a mutex-suppressed attempt returns before this line), so counting that log
// line is the direct replacement for the old syncMessages-call-count assertions.
function healStartCount(warnSpy, sid) {
  return warnSpy.mock.calls.filter(
    ([msg]) => typeof msg === 'string' && msg.includes(`[stall-heal] Session ${sid} stalled`)
  ).length
}

// Issue #1974: advances time in STALL_CHECK_INTERVAL_MS (5000ms) steps and calls
// checkSessionStall() on each tick, rather than one large time jump. Under the new
// tick-lateness forgiveness logic, any single tick gap beyond STALL_CHECK_INTERVAL_MS +
// TICK_LATE_BUFFER_MS is treated as a possible freeze and forgiven from the staleness
// computation — so a single big jump is now indistinguishable from a freeze, by design.
// This simulates "many on-time ticks with no successful poll in between," which is what
// a genuine active-tab stall actually looks like. Awaits every tick, including the last.
async function tickStallCadence(pollingStore, ticks) {
  for (let i = 0; i < ticks; i++) {
    advanceTime(5000)
    await pollingStore.checkSessionStall()
  }
}

// Issue #1979: delivers a single 'session_reset' UI-poll event for sessionId, while any
// concurrent session-poll fetch ('/api/poll/session/...') keeps hanging exactly like
// abortAwareFetchMock — used by both the in-flight-heal race test and the regression-guard
// test below, which differ only in what state they set up beforehand.
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

async function setupStallSession(sessionOverrides) {
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

describe('polling store - backendStatus (issue #1989)', () => {
  it('starts as ok', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    expect(pollingStore.backendStatus).toBe('ok')
  })

  it('a UI-poll response carrying backend_status updates it', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedFetchMock([
      { events: [], next_cursor: 1, backend_status: 'unreachable' },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.backendStatus).toBe('unreachable')
  })

  it('a UI-poll response carrying backend_status: degraded updates it', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedFetchMock([
      { events: [], next_cursor: 1, backend_status: 'degraded' },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.backendStatus).toBe('degraded')
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
    expect(pollingStore.backendStatus).toBe('unreachable')

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 2, backend_status: 'ok' }) })
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.backendStatus).toBe('ok')
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

    expect(pollingStore.backendStatus).toBe('ok')
  })

  it('a session-poll response carrying backend_status updates it', async () => {
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
    await pollingStore.disconnectSession()

    expect(pollingStore.backendStatus).toBe('unreachable')
  })

  // Regression test (builder-review finding): the session-poll loop's backendStatus
  // write originally happened before the isCurrentGeneration guard used for
  // heartbeat/sessionStalled just below it — a stale response landing after this
  // session was superseded (e.g. by a stall-heal reconnect, or a session switch)
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
    pollingStore.backendStatus = 'ok'

    // Simulate this session no longer being current (e.g. a stall-heal reconnect or a
    // session switch) while the poll request above is still in flight.
    pollingStore.currentSessionId = 'some-other-session'

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1, backend_status: 'unreachable' }) })
    await flush()

    expect(pollingStore.backendStatus).toBe('ok')
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
  it('overallStatus does not read "connected" when uiConnected but appDataStatus is failed (AC4)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    pollingStore.uiConnected = true
    uiStore.setAppDataStatus('failed')

    expect(pollingStore.overallStatus).not.toBe('connected')
    expect(pollingStore.overallStatus).toBe('partial')
  })

  it('overallStatus reads connected when uiConnected and appDataStatus is loaded', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    pollingStore.uiConnected = true
    uiStore.setAppDataStatus('loaded')

    expect(pollingStore.overallStatus).toBe('connected')
  })

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

      // Three backoff cycles (2000ms, 4000ms, 6000ms) — each re-arms uiConnected
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

  it('T1: heals a stalled active-processing connection once the unified threshold elapses', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-t1', is_processing: true })
    const warnSpy = vi.spyOn(console, 'warn')
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    // past the unified STALL_TIMEOUT_MS (40s) via realistic tick cadence (Issue #1974 —
    // a single big jump is indistinguishable from a freeze under the new tick-lateness
    // logic) — intentional behavior change from the old 15s active-processing threshold:
    // is_processing no longer selects a faster threshold, since the heartbeat measures
    // poll round-trip freshness (bounded by the server's 30s clamp) which behaves the
    // same whether the session is active or idle.
    await tickStallCadence(pollingStore, 8)
    await flush()

    expect(healStartCount(warnSpy, sid)).toBe(1)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('T2: heals a stalled idle connection once the unified threshold elapses', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-t2', is_processing: false })
    const warnSpy = vi.spyOn(console, 'warn')
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (Issue #1974) — old
    // is_processing-gated code would never have caught this
    await tickStallCadence(pollingStore, 8)
    await flush()

    expect(healStartCount(warnSpy, sid)).toBe(1)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('T3: does not heal a genuinely idle but healthy connection', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-t3', is_processing: false })

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
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-t3b', is_processing: true })

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

  it('T4 (#1988): end-to-end heal cycle reconnects straight from the existing cursor — no history walk, no extra cursor GET, no manual loadMessages reload', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-t4', is_processing: false })
    const loadSpy = vi.spyOn(messageStore, 'loadMessages')
    const fetchSpy = abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974)
    await flush()

    expect(loadSpy).not.toHaveBeenCalled()
    expect(pollingStore.sessionConnected).toBe(true)

    // Root-cause fix: the resumed poll's `since` is the SAME cursor connectSession()
    // bootstrapped before the stall (0, from setupStallSession's cursor mock) — not a
    // freshly re-fetched value from a second /cursor GET, and not derived from any
    // REST history walk.
    const lastFetchUrl = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1][0]
    expect(lastFetchUrl).toContain('since=0')

    // Only the initial connectSession() bootstrap should have hit /cursor — the heal
    // must not issue a second one, and must never touch the /messages history endpoint.
    const cursorRequests = apiMock.get.mock.calls.filter(([url]) => String(url).includes('/cursor'))
    const messagesRequests = apiMock.get.mock.calls.filter(([url]) => String(url).includes('/messages'))
    expect(cursorRequests).toHaveLength(1)
    expect(messagesRequests).toHaveLength(0)
  })

  it('B1 (#1917): aborts the in-flight fetch synchronously at the start of the heal sequence, before any await', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-b1', is_processing: false })

    // A fetch that only ever settles via its abort signal — never resolves on its own —
    // so we can prove the abort happens before the heal's first await. #1988 removed the
    // syncMessages()/cursor-GET steps that used to sit between the synchronous abort and
    // the reconnect — disconnectSession() is now that first await, so this test proves
    // the abort still precedes it.
    let capturedSignal
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
      capturedSignal = opts?.signal
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    })

    await pollingStore.connectSession(sid)
    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below so its synchronous
    // portion (the abort) can be inspected before awaiting the rest of the heal.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    // Do not await yet — an async function runs synchronously up to its first `await`,
    // so by the time this call returns control, B1's synchronous abort has already run.
    const healPromise = pollingStore.checkSessionStall()

    expect(capturedSignal.aborted).toBe(true)

    // Let the heal sequence finish so it doesn't leak into later tests.
    await healPromise
  })

  it('#1954: suppresses an overlapping heal while one is already in flight, regardless of cooldown', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-inflight', is_processing: false })
    const warnSpy = vi.spyOn(console, 'warn')

    // Issue #1988: the pre-heal (old-generation) fetch never reacts to its abort
    // signal — it only settles once resolveOldFetch() is called — so
    // disconnectSession()'s wait for the old poll loop to exit stays pending for as
    // long as this test needs. This is the #1988-era replacement for the old
    // syncMessages()-hang technique: the new heal body has no REST await of its own
    // left to freeze on, since #1988 removed the two REST calls that used to sit
    // between the synchronous abort and the reconnect. Note: because
    // disconnectSession() now runs almost immediately (rather than after two REST
    // round trips), sessionConnected also reads false for most of this window now —
    // the older #1795 "not connected" guard and the #1954 mutex largely overlap in
    // what they protect against today. This test still directly exercises and proves
    // the mutex's own contract: no second heal starts while sessionHealInFlight is true.
    let resolveOldFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveOldFetch = resolve }))

    await pollingStore.connectSession(sid)
    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    // First heal starts and blocks inside disconnectSession(), waiting for the old
    // poll loop to exit — it never does during this assertion window, simulating the
    // ~31s real-world heal duration from #1931's capture.
    const healPromise1 = pollingStore.checkSessionStall()

    expect(healStartCount(warnSpy, sid)).toBe(1)

    // Advance past HEAL_COOLDOWN_MS (10s) — the cooldown alone would now permit a new
    // heal, but the in-flight guard must still suppress it since the first heal hasn't
    // finished. This is exactly the overlap race from #1954.
    advanceTime(11000)
    await pollingStore.checkSessionStall()

    expect(healStartCount(warnSpy, sid)).toBe(1)

    // Let the first heal complete normally — it should finish exactly as it would have
    // without the new guard.
    resolveOldFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 0 }) })
    await healPromise1
    await flush()

    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('#1954: releases the guard after a completed heal, allowing a genuine subsequent heal', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-second-heal', is_processing: false })
    const warnSpy = vi.spyOn(console, 'warn')
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974)
    await flush()

    expect(healStartCount(warnSpy, sid)).toBe(1)

    // #1973: the reconnect at the end of a heal no longer reseeds the heartbeat — the
    // stale pre-heal value is preserved until a real poll response updates it, so the
    // staleness (already past STALL_TIMEOUT_MS against that stale baseline) persists
    // across the reconnect. Only HEAL_COOLDOWN_MS (10s), tracked separately via
    // lastHealedAt, still gates the second heal — two more realistic ticks clears it.
    await tickStallCadence(pollingStore, 2)
    await flush()

    expect(healStartCount(warnSpy, sid)).toBe(2)
  })

  // Issue #1960: sessionStalled reporting — decoupled from the healing action above.
  it('#1960: sessionStalled becomes true synchronously once the threshold elapses, before the heal reconnects', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-a', is_processing: false })

    // Issue #1988: hang the pre-heal fetch without reacting to its abort signal, so
    // disconnectSession() (the heal's first await now, since #1988 removed the
    // syncMessages()/cursor-GET steps that used to precede it) stays pending for this
    // assertion window.
    let resolveOldFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveOldFetch = resolve }))

    await pollingStore.connectSession(sid)
    expect(pollingStore.sessionStalled).toBe(false)

    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    // sessionStalled is set synchronously, before the heal sequence's first await —
    // assert here (mid-heal) to isolate that synchronous set from whatever the eventual
    // reconnect/poll outcome does to the flag (mirrors the B1 pattern above; see the
    // #1973 tests for reconnect-specific behavior). Note sessionConnected is NOT
    // asserted true here anymore: #1988's redesign makes disconnectSession() the heal's
    // first await, and disconnectSession() synchronously flips sessionConnected false
    // as part of its own prefix — so by this point it already reads false, unlike the
    // old, REST-heavy heal where it stayed true for most of the heal's duration.
    const healPromise = pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)

    resolveOldFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 0 }) })
    await healPromise

    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('#1960: sessionStalled stays false for a healthy-but-quiet connection', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-b', is_processing: false })

    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)

    for (let i = 0; i < 3; i++) {
      advanceTime(25000) // simulate the server holding the long-poll request open with no events
      resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: i + 1 }) })
      await flush()
      await pollingStore.checkSessionStall()
      expect(pollingStore.sessionStalled).toBe(false)
    }
  })

  it('#1960: sessionStalled clears immediately on the next successful poll, without waiting for a watchdog tick', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-c', is_processing: false })

    let resolveFetch
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => new Promise((resolve, reject) => {
      resolveFetch = resolve
      opts?.signal?.addEventListener('abort', () => {
        const err = new Error('Aborted')
        err.name = 'AbortError'
        reject(err)
      })
    }))

    await pollingStore.connectSession(sid)
    // Set directly rather than driving it via checkSessionStall(), since a real stall
    // detection at this threshold would also trigger the heal cycle's own reconnect —
    // this test targets only _runSessionPollLoop's heartbeat-refresh clearing behavior.
    pollingStore.sessionStalled = true

    resolveFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 1 }) })
    await flush()

    expect(pollingStore.sessionStalled).toBe(false)
    expect(fetchSpy).toHaveBeenCalled()
  })

  it('#1960: sessionStalled is false while session.state is paused even if the heartbeat is stale', async () => {
    const { pollingStore, sessionStore, sid } = await setupStallSession({ session_id: 'sess-1960-d', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000)

    sessionStore.sessions.set(sid, makeSession({ session_id: sid, state: 'paused' }))
    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1960: sessionStalled does not suppress the existing heal-gating early return for #1795', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-e', is_processing: false })
    abortAwareFetchMock()
    const warnSpy = vi.spyOn(console, 'warn')

    await pollingStore.connectSession(sid)
    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    // Simulate the exponential-backoff loop having already detected a failure.
    pollingStore.sessionConnected = false

    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(healStartCount(warnSpy, sid)).toBe(0)
  })

  it('#1960: a session switch does not leak sessionStalled=true onto the newly selected session', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-f', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    // Set directly rather than via checkSessionStall(), since triggering a real heal
    // would itself reconnect this same session and reset the flag before the session
    // switch under test even happens — this test targets connectSession()'s own guard.
    pollingStore.sessionStalled = true

    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore()
    sessionStore.sessions.set('sess-1960-g', makeSession({ session_id: 'sess-1960-g' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    await pollingStore.connectSession('sess-1960-g')

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1960: stopStallDetector() clears the interval so checkSessionStall stops firing after disconnectSession()', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-h', is_processing: false })
    abortAwareFetchMock()

    const clearIntervalSpy = vi.spyOn(global, 'clearInterval')
    await pollingStore.connectSession(sid)
    await pollingStore.disconnectSession()

    expect(clearIntervalSpy).toHaveBeenCalled()
  })

  it('#1960: the no-heartbeat branch is reachable and leaves sessionStalled false rather than stale', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-i', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    // resetSessionCursor() deletes the seeded heartbeat entry without clearing
    // currentSessionId, reproducing the "session current but heartbeat not yet
    // recorded" state the no-heartbeat branch exists for.
    pollingStore.resetSessionCursor(sid)

    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1960: resetSessionCursor() clears a previously-set sessionStalled flag directly', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1960-j', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    pollingStore.sessionStalled = true

    pollingStore.resetSessionCursor(sid)

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1974: cleanupSessionPollingState() clears sessionStalled when the cleaned-up session is the current one', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-cleanup-current', is_processing: false })

    // Sets the property under test directly (currentSessionId, sessionStalled) rather than
    // driving a full connectSession()/fetch-mock cycle — isolates this test from unrelated
    // polling-loop internals (retry state, cursor bootstrap, stall detector startup).
    pollingStore.currentSessionId = sid
    pollingStore.sessionStalled = true

    pollingStore.cleanupSessionPollingState(sid)

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1974: cleanupSessionPollingState() does not clear sessionStalled when cleaning up a different, non-current session (deleteSession cascade)', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-cleanup-a', is_processing: false })

    pollingStore.currentSessionId = sid
    pollingStore.sessionStalled = true

    // deleteSession() can cascade-delete several sessions at once (e.g. a project delete),
    // most of which are not the one currently displayed — unlike resetSessionCursor()
    // above, cleaning up an unrelated sibling session must not wrongly clear the
    // genuinely-stalled flag for the session actually being viewed.
    pollingStore.cleanupSessionPollingState('sess-1974-cleanup-b')

    expect(pollingStore.sessionStalled).toBe(true)
  })

  it("#1973: a heal reconnect that doesn't restore data leaves sessionStalled true", async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1973-a', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974)
    await flush()

    // The heal's reconnect must not forge liveness — no successful poll response has
    // occurred yet, so the flag must stay true immediately after the heal returns.
    expect(pollingStore.sessionStalled).toBe(true)

    // A subsequent watchdog tick against the still-stale (unreseeded) heartbeat must
    // not flip it false either.
    advanceTime(5000) // STALL_CHECK_INTERVAL_MS
    await pollingStore.checkSessionStall()
    await flush()

    expect(pollingStore.sessionStalled).toBe(true)
  })

  it('#1973: a heal reconnect followed by a real poll response clears sessionStalled promptly', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1973-b', is_processing: false })

    let callCount = 0
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
      callCount++
      if (callCount === 2) {
        // The heal reconnect's first fetch — the one real poll response under test —
        // succeeds immediately.
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: callCount }) })
      }
      // Call 1 (pre-heal connection) hangs until aborted by the heal sequence; calls
      // from 3 onward (post-recovery loop continuation) hang too, so the loop parks
      // instead of spinning a tight, unbounded fetch loop once it "recovers".
      return new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    })

    await pollingStore.connectSession(sid)
    advanceTime(41000) // past STALL_TIMEOUT_MS (40s)

    await pollingStore.checkSessionStall()
    await flush()

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1973: repeated unsuccessful heals never let sessionStalled flip false between attempts', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1973-c', is_processing: false })
    const warnSpy = vi.spyOn(console, 'warn')
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974) — first heal
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    advanceTime(5000) // STALL_CHECK_INTERVAL_MS tick between heals, still no data
    await pollingStore.checkSessionStall()
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    // One more realistic tick clears HEAL_COOLDOWN_MS since the first heal — second heal
    await tickStallCadence(pollingStore, 1)
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    expect(healStartCount(warnSpy, sid)).toBe(2)
  })

  it("#1973: baseline is still seeded on a session's first connection", async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1973-d', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    // measurable only if the baseline was seeded at connect time — regression guard for
    // #1795, driven via realistic tick cadence (#1974)
    await tickStallCadence(pollingStore, 8)

    expect(pollingStore.sessionStalled).toBe(true)
  })
})

describe('polling store - freeze forgiveness watchdog (#1974)', () => {
  // Same fake-timer setup as the #1795 describe block above — Date only, so the
  // background stall-detector interval (real setTimeout/setInterval) doesn't fire mid-test.
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(1_700_000_000_000)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('backgrounded tab, healthy connection: a single freeze gap is not misread as a stall', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-a', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    // The tab is backgrounded/suspended for a full minute — no watchdog tick ran during
    // that time (that's exactly what a freeze is), so the very next tick observes one
    // large gap instead of the usual 5s cadence.
    advanceTime(60000)
    await pollingStore.checkSessionStall()
    expect(pollingStore.sessionStalled).toBe(false)

    // Resume normal cadence after the freeze — still healthy.
    advanceTime(5000)
    await pollingStore.checkSessionStall()
    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('sleeping machine with document.hidden still false: freeze forgiveness alone (not visibility) prevents a false stall', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-b', is_processing: false })
    abortAwareFetchMock()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)

    await pollingStore.connectSession(sid)

    advanceTime(60000) // the machine sleeps; the tab stays nominally visible throughout
    await pollingStore.checkSessionStall()

    expect(document.hidden).toBe(false)
    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('a realistic single machine-sleep gap well under MAX_FORGIVE_PER_TICK_MS (30min) is still fully forgiven', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-cap-under', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    advanceTime(25 * 60 * 1000) // a 25-minute sleep — under the 30-minute per-tick cap
    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('a pathological single freeze far exceeding MAX_FORGIVE_PER_TICK_MS (30min) is bounded rather than forgiven forever', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-cap-over', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    advanceTime(45 * 60 * 1000) // a 45-minute freeze — well past the 30-minute per-tick cap
    await pollingStore.checkSessionStall()

    // Only 30 minutes of this gap is forgiven; the remaining ~15 minutes counts as real
    // staleness, which already far exceeds STALL_TIMEOUT_MS (40s) on this same tick —
    // an unbounded cap would instead have forgiven the entire 45 minutes and read healthy.
    expect(pollingStore.sessionStalled).toBe(true)
  })

  it('a genuinely dead connection under sustained ~60s-cadence background throttling still converges to stalled within a bounded number of ticks', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-throttle-dead', is_processing: false })
    abortAwareFetchMock() // the connection itself is dead — the heartbeat never refreshes

    await pollingStore.connectSession(sid)

    // Simulate Chrome's documented background-tab timer clamp: the watchdog interval
    // still fires, just throttled to roughly once per minute instead of every 5s. Each
    // individual 60s gap stays far under the 30-minute per-tick cap, so per-tick
    // forgiveness is unaffected by the cap (53s forgiven, 7s counted each tick, same as
    // before the cap was added) — this only confirms the cap doesn't regress that
    // existing convergence. At exactly 7s counted per tick, stallMs crosses
    // STALL_TIMEOUT_MS (40s) on tick 6 (42s), not before — pinned exactly so any drift in
    // the cap/buffer math (e.g. an off-by-one that halves effective forgiveness) fails
    // this test immediately instead of passing silently under a loose bound.
    for (let i = 0; i < 5; i++) {
      advanceTime(60000)
      await pollingStore.checkSessionStall()
      expect(pollingStore.sessionStalled).toBe(false)
    }
    advanceTime(60000)
    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
  })

  it('a stall already underway before the tab is hidden is still reported after a subsequent freeze/return', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-c', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    // A genuine stall accumulates first, at normal cadence — no freeze involved.
    await tickStallCadence(pollingStore, 8) // reaches STALL_TIMEOUT_MS (40s)
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    // The tab now freezes and the user returns — the freeze/return must not clear a
    // stall that was already genuinely underway before the freeze started.
    advanceTime(60000)
    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
  })

  it("a freeze followed by return doesn't fabricate health when the connection is actually broken", async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-d', is_processing: false })
    const warnSpy = vi.spyOn(console, 'warn')
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    // A freeze shorter than the real outage — forgiven, no heal/stall reported yet.
    advanceTime(60000)
    await pollingStore.checkSessionStall()
    expect(pollingStore.sessionStalled).toBe(false)

    // The connection stays broken (fetch keeps hanging, never delivers) well past the
    // freeze — the watchdog must still catch it via normal cadence, just not immediately
    // on return.
    await tickStallCadence(pollingStore, 8)
    await flush()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(healStartCount(warnSpy, sid)).toBe(1)
  })

  it('a backgrounded tab does not trigger heal recovery even once genuinely stalled, but keeps reporting the stall', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-e', is_processing: false })
    abortAwareFetchMock()
    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    const warnSpy = vi.spyOn(console, 'warn')

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // reaches STALL_TIMEOUT_MS (40s) at normal cadence
    await flush()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(healStartCount(warnSpy, sid)).toBe(0)

    hiddenSpy.mockRestore()
  })

  it("a stale in-flight response from a superseded polling generation can't overwrite the heartbeat/frozen snapshot or clear sessionStalled", async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1974-race', is_processing: false })

    // The pre-heal (old-generation) fetch never reacts to abort — it simulates having
    // already progressed past the fetch by the time sessionAbortController.abort() fires
    // (e.g. mid `await response.json()`), so the abort has no effect and this response
    // resolves successfully on its own, landing AFTER the heal has already bumped the
    // generation and reconnected. Every fetch after the first (the heal's own reconnect
    // and beyond) just hangs — this test doesn't need to model further polling.
    let resolveOldFetch
    let fetchCallCount = 0
    vi.spyOn(global, 'fetch').mockImplementation(() => {
      fetchCallCount++
      if (fetchCallCount === 1) {
        return new Promise(resolve => { resolveOldFetch = resolve })
      }
      return new Promise(() => {})
    })

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // reaches STALL_TIMEOUT_MS (40s) — starts the heal
    await flush()

    // The heal reconnected (isRecoveryReconnect) without a real response yet, so the
    // heartbeat/frozen snapshot are still stale and sessionStalled must still read true.
    expect(pollingStore.sessionStalled).toBe(true)

    // The OLD generation's fetch (from before the heal) now finally resolves successfully.
    // Without the generation guard, this stale success would wrongly refresh the
    // heartbeat/frozen snapshot and clear sessionStalled directly (bypassing the watchdog
    // entirely). With the guard, nothing should change.
    resolveOldFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 999 }) })
    await flush()

    expect(pollingStore.sessionStalled).toBe(true)
  })

  it('#1979: a session_reset event for a session whose stall-heal is still in flight does not erase the heartbeat/frozen snapshot or clear sessionStalled', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1979-a', is_processing: false })

    // Issue #1988: the pre-heal fetch never reacts to its abort signal, so
    // disconnectSession() (the heal's first await now that the syncMessages()/cursor-GET
    // steps are gone) stays pending until resolveOldFetch() is called — keeping
    // sessionHealInFlight[sid] true for the whole assertion window below, mirroring the
    // '#1960: sessionStalled becomes true...' test's technique further up this file.
    let resolveOldFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveOldFetch = resolve }))

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 7)

    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s), starts the heal
    const healPromise = pollingStore.checkSessionStall()
    expect(pollingStore.sessionStalled).toBe(true) // heal in flight, heartbeat still stale

    // Feed a session_reset UI-poll event for the same session while the heal is in flight.
    await fireSessionResetEvent(pollingStore, sid)

    expect(pollingStore.sessionStalled).toBe(true)

    // Let the heal finish cleanly.
    resolveOldFetch({ ok: true, json: () => Promise.resolve({ events: [], next_cursor: 0 }) })
    await healPromise

    // The heartbeat/frozen snapshot survived the session_reset: a subsequent
    // checkSessionStall() tick still reports genuinely stalled, rather than the spurious
    // "no heartbeat" false that would result if session_reset had deleted them mid-heal
    // (see the '#1960: the no-heartbeat branch...' test above for that contrasting case).
    advanceTime(5000)
    await pollingStore.checkSessionStall()
    expect(pollingStore.sessionStalled).toBe(true)
  })

  it('#1979: a session_reset event with no heal in flight still clears the heartbeat/stalled state as before (regression guard)', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1979-b', is_processing: false })
    const { useResourceStore } = await import('@/stores/resource')
    const { useEditHistoryStore } = await import('@/stores/editHistory')
    const { useSessionStore } = await import('@/stores/session')
    const resourceStore = useResourceStore()
    const editHistoryStore = useEditHistoryStore()
    const sessionStore = useSessionStore()
    const clearMessagesSpy = vi.spyOn(messageStore, 'clearMessages')
    const clearResourcesSpy = vi.spyOn(resourceStore, 'clearResources')
    const clearHistorySpy = vi.spyOn(editHistoryStore, 'clearHistory')
    const recordResetSpy = vi.spyOn(sessionStore, 'recordSessionReset')

    abortAwareFetchMock()
    await pollingStore.connectSession(sid)
    // Set directly rather than driving a real stall/heal — this test only cares about
    // session_reset's own clearing behavior when sessionHealInFlight is falsy, mirroring
    // the '#1960: sessionStalled clears immediately...' test's technique above.
    pollingStore.sessionStalled = true

    await fireSessionResetEvent(pollingStore, sid)

    expect(pollingStore.sessionStalled).toBe(false)
    expect(clearMessagesSpy).toHaveBeenCalledWith(sid)
    expect(clearResourcesSpy).toHaveBeenCalledWith(sid)
    expect(clearHistorySpy).toHaveBeenCalledWith(sid)
    expect(recordResetSpy).toHaveBeenCalledWith(sid)
  })
})

// Issue #1988: root-cause fix — a stall-heal no longer reconciles via a REST history
// walk (syncMessages()) followed by a separately-timed cursor re-fetch. Both steps are
// gone; the heal reconnects straight from the existing, untouched sessionCursors[sid],
// trusting EventQueue.events_since()'s atomic "everything after cursor X, right now"
// semantics (shared/event_queue.py) to deliver a gapless, exactly-once catch-up batch on
// the resumed poll's very first request — including anything appended during the heal
// window itself. A cursor old enough to have been evicted from the server's ring buffer
// is handled by a separate, explicit fallback (see poll.js's `evicted` handling below).
describe('polling store - stall-heal cursor-atomicity fix (#1988)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(1_700_000_000_000)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('T1: every event appended during the heal window is delivered exactly once via the resumed poll, with zero history/sync REST calls (AC1, AC2)', async () => {
    const { pollingStore, sessionStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1988-t1', is_processing: false })
    // handleSessionMessage() only applies an event when session.js's own currentSessionId
    // (independent of polling.js's) matches — mirrors how the real app keeps them in sync
    // via selectSession().
    sessionStore.currentSessionId = sid
    const addMessageSpy = vi.spyOn(messageStore, 'addMessage')

    sequencedFetchMock([
      { events: [], next_cursor: 5 }, // initial connect's poll — establishes cursor 5
      // second iteration (pre-stall) hangs — falls through to sequencedFetchMock's
      // abort-aware default once the array below is exhausted
      (url, opts) => new Promise((_resolve, reject) => {
        opts?.signal?.addEventListener('abort', () => {
          const err = new Error('Aborted')
          err.name = 'AbortError'
          reject(err)
        })
      }),
      // The heal's resumed poll — its very first request reuses cursor 5 unchanged (no
      // REST re-derivation) and delivers an event that was appended to the server
      // during the heal window itself.
      (url) => {
        expect(String(url)).toContain('since=5')
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            events: [{ type: 'message', data: { type: 'assistant', id: 'during-heal-1' } }],
            next_cursor: 6,
          }),
        })
      },
    ])

    await pollingStore.connectSession(sid)
    await flush() // let the first poll response land (cursor becomes 5), loop issues 2nd call which hangs

    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s)
    await flush()

    expect(addMessageSpy).toHaveBeenCalledWith(sid, expect.objectContaining({ id: 'during-heal-1' }))
    // Zero REST calls to the message-history endpoint — the old two-step
    // (syncMessages + cursor GET) is gone from the common path entirely.
    const messagesRequests = apiMock.get.mock.calls.filter(([url]) => String(url).includes('/messages'))
    expect(messagesRequests).toHaveLength(0)
  })

  it('T2: the heal path issues zero REST history-page requests even for a long-lived/large session, only the reconnect + resumed poll (AC3)', async () => {
    const { pollingStore, sid } = await setupStallSession({ session_id: 'sess-1988-t2', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8)
    await flush()

    // A 20,000-event session would otherwise cost many sequential /messages page round
    // trips under the old syncMessages()-based heal — the new heal never walks history
    // regardless of session size, so this count must be zero.
    const messagesRequests = apiMock.get.mock.calls.filter(([url]) => String(url).includes('/messages'))
    expect(messagesRequests).toHaveLength(0)
    // Only the initial connectSession() bootstrap should have hit /cursor — no second,
    // heal-specific re-fetch.
    const cursorRequests = apiMock.get.mock.calls.filter(([url]) => String(url).includes('/cursor'))
    expect(cursorRequests).toHaveLength(1)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('evicted fallback: a poll response reporting evicted=true triggers a full history reload and adopts the reload\'s own event_cursor', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1988-evicted', is_processing: false })
    const loadSpy = vi.spyOn(messageStore, 'loadMessages').mockImplementation(async (sessionId) => {
      // Mirrors loadMessages()'s real event_cursor-capturing contract (message.js) without
      // exercising its full REST/parsing pipeline — that pipeline has its own coverage in
      // message.test.js; this test is about polling.js's response to the `evicted` flag.
      messageStore.loadedEventCursors.set(sessionId, 999)
      return { messages: [], totalCount: 0, hasMore: false }
    })

    const fetchMock = sequencedFetchMock([
      { events: [], next_cursor: 5 }, // initial connect poll establishes cursor 5
      { events: [], next_cursor: 5, evicted: true }, // next poll: cursor 5 has been evicted server-side
    ])

    await pollingStore.connectSession(sid)
    await flush() // first response lands (cursor -> 5)
    await flush() // second (evicted) response lands, triggers the fallback reload

    expect(loadSpy).toHaveBeenCalledWith(sid)

    // The next poll cycle resumes from the RELOAD's own event_cursor (999) — not the
    // evicted response's own next_cursor (5), and not a stale re-fetch.
    const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    expect(lastCall[0]).toContain('since=999')
  })

  it('evicted fallback: does not apply the evicted response\'s own (incomplete) events — only the fresh reload\'s state', async () => {
    const { pollingStore, sessionStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1988-evicted-b', is_processing: false })
    sessionStore.currentSessionId = sid // so a false negative can't hide behind handleSessionMessage()'s own currentSessionId guard
    const addMessageSpy = vi.spyOn(messageStore, 'addMessage')
    vi.spyOn(messageStore, 'loadMessages').mockImplementation(async (sessionId) => {
      messageStore.loadedEventCursors.set(sessionId, 42)
      return { messages: [], totalCount: 0, hasMore: false }
    })

    sequencedFetchMock([
      { events: [], next_cursor: 5 },
      {
        // The buffered-but-incomplete batch a real evicted response would still carry —
        // must NOT be applied via handleSessionMessage(), since it can't be trusted as a
        // gapless slice once the server has already dropped some history.
        events: [{ type: 'message', data: { type: 'assistant', id: 'unreliable-evicted-event' } }],
        next_cursor: 6,
        evicted: true,
      },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(addMessageSpy).not.toHaveBeenCalledWith(sid, expect.objectContaining({ id: 'unreliable-evicted-event' }))
  })

  // Note: #1917's synchronous-abort guarantee and #1956/#1954's overlapping-heal mutex
  // are already directly re-verified above by 'B1 (#1917): ...' and '#1954: suppresses
  // an overlapping heal...' against this same new heal body — not duplicated again here.

  it('reset fallback: a poll response reporting reset=true (evicted=false) also triggers the full history reload — Backend-restart-during-stall', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1988-reset-fallback', is_processing: false })
    const loadSpy = vi.spyOn(messageStore, 'loadMessages').mockImplementation(async (sessionId) => {
      messageStore.loadedEventCursors.set(sessionId, 7)
      return { messages: [], totalCount: 0, hasMore: false }
    })

    const fetchMock = sequencedFetchMock([
      { events: [], next_cursor: 5 }, // initial connect poll establishes cursor 5
      // A Backend restart recreated the session's EventQueue from scratch: reset=true,
      // but evicted is deliberately false — reset and eviction are independent signals
      // (shared/event_queue.py) — so this must NOT be silently treated as "here's
      // everything" the way an ordinary reset (outside a heal) would be.
      { events: [], next_cursor: 0, reset: true, evicted: false },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(loadSpy).toHaveBeenCalledWith(sid)
    const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
    expect(lastCall[0]).toContain('since=7')
  })

  it('regression: a concurrent reconnect during the evicted/reset reload does not have its cursor clobbered by the stale reload once it resolves', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1988-race-fix', is_processing: false })

    let resolveLoad
    vi.spyOn(messageStore, 'loadMessages').mockImplementation(() => new Promise(resolve => {
      // Deliberately does NOT set messageStore.loadedEventCursors — isolates this test
      // to the direct `sessionCursors[sessionId] = data.next_cursor` write path (the
      // one the post-await generation guard protects), so it can't pass "by accident"
      // via connectSession()'s own separate, unguarded loadedEventCursors consumption.
      resolveLoad = () => resolve({ messages: [], totalCount: 0, hasMore: false })
    }))

    const fetchMock = sequencedFetchMock([
      { events: [], next_cursor: 5 }, // initial connect poll establishes cursor 5
      { events: [], next_cursor: 5, evicted: true }, // triggers the fallback reload, held open
    ])

    await pollingStore.connectSession(sid)
    await flush() // cursor -> 5; evicted response starts the now-pending reload

    // A concurrent reconnect for the same session (e.g. a stall-heal) supersedes this
    // loop's generation while the reload above is still in flight.
    pollingStore.resetSessionCursor(sid)
    apiMock.get.mockResolvedValue({ cursor: 42 })
    await pollingStore.connectSession(sid)

    const newGenFetchUrl = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]
    expect(newGenFetchUrl).toContain('since=42') // sanity: the new generation really did start from 42

    // Now let the STALE (superseded-generation) reload finally resolve.
    resolveLoad()
    await flush()

    // Force a fresh poll cycle to observe whichever cursor is currently in effect. If
    // the stale reload's post-await write weren't guarded, sessionCursors[sid] would
    // have been clobbered back to 5 (the old evicted response's own next_cursor).
    await pollingStore.disconnectSession()
    await pollingStore.connectSession(sid)
    const finalFetchUrl = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]
    expect(finalFetchUrl).toContain('since=42')
  })
})
