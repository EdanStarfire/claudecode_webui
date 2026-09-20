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
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-t1', is_processing: true })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
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

    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('T2: heals a stalled idle connection once the unified threshold elapses', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-t2', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)

    // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (Issue #1974) — old
    // is_processing-gated code would never have caught this
    await tickStallCadence(pollingStore, 8)
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
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

  it('T4: end-to-end heal cycle re-syncs via syncMessages without a manual loadMessages reload', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-t4', is_processing: false })
    apiMock.get.mockResolvedValueOnce({ cursor: 0 }).mockResolvedValue({ cursor: 12 })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 2, hasMore: false })
    const loadSpy = vi.spyOn(messageStore, 'loadMessages')
    const fetchSpy = abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974)
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
    expect(loadSpy).not.toHaveBeenCalled()
    expect(pollingStore.sessionConnected).toBe(true)
    const lastFetchUrl = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1][0]
    expect(lastFetchUrl).toContain('since=12')
  })

  it('B1 (#1917): aborts the in-flight fetch synchronously at the start of the heal sequence, before any await', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-b1', is_processing: false })

    // A fetch that only ever settles via its abort signal — never resolves on its own —
    // so we can prove the abort happens before syncMessages() is even awaited, not only
    // later inside disconnectSession() (the pre-fix behavior this test guards against).
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

    // syncMessages() deliberately never resolves during this assertion window.
    let resolveSync
    vi.spyOn(messageStore, 'syncMessages').mockImplementation(() => new Promise(resolve => { resolveSync = resolve }))

    // Do not await yet — an async function runs synchronously up to its first `await`,
    // so by the time this call returns control, B1's synchronous abort has already run.
    const healPromise = pollingStore.checkSessionStall()

    expect(capturedSignal.aborted).toBe(true)

    // Let the heal sequence finish so it doesn't leak into later tests.
    resolveSync({ syncedCount: 0, hasMore: false })
    apiMock.get.mockResolvedValue({ cursor: 0 })
    await healPromise
  })

  it('#1954: suppresses an overlapping heal while one is already in flight, regardless of cooldown', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-inflight', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    let resolveSync
    vi.spyOn(messageStore, 'syncMessages').mockImplementation(() => new Promise(resolve => { resolveSync = resolve }))

    // First heal starts and blocks on syncMessages — it never resolves during this
    // assertion window, simulating the ~31s real-world heal duration from #1931's capture.
    const healPromise1 = pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(1)

    // Advance past HEAL_COOLDOWN_MS (10s) — the cooldown alone would now permit a new
    // heal, but the in-flight guard must still suppress it since the first heal hasn't
    // finished. This is exactly the overlap race from #1954.
    advanceTime(11000)
    await pollingStore.checkSessionStall()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(1)

    // Let the first heal complete normally — it should finish exactly as it would have
    // without the new guard.
    apiMock.get.mockResolvedValue({ cursor: 0 })
    resolveSync({ syncedCount: 0, hasMore: false })
    await healPromise1
    await flush()

    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('#1954: releases the guard after a completed heal, allowing a genuine subsequent heal', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-second-heal', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974)
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(1)

    // #1973: the reconnect at the end of a heal no longer reseeds the heartbeat — the
    // stale pre-heal value is preserved until a real poll response updates it, so the
    // staleness (already past STALL_TIMEOUT_MS against that stale baseline) persists
    // across the reconnect. Only HEAL_COOLDOWN_MS (10s), tracked separately via
    // lastHealedAt, still gates the second heal — two more realistic ticks clears it.
    await tickStallCadence(pollingStore, 2)
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(2)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('#1954: releases the guard after a heal error, allowing a genuine subsequent heal', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-heal-error', is_processing: false })
    abortAwareFetchMock()
    vi.spyOn(messageStore, 'syncMessages').mockRejectedValueOnce(new Error('sync failed'))

    await pollingStore.connectSession(sid)

    // syncMessages() rejects, but checkSessionStall() catches it internally (line
    // 316-318-equivalent try/catch) so the heal cycle still reaches its finally block.
    await tickStallCadence(pollingStore, 8) // past STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974)
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(1)
    expect(pollingStore.sessionConnected).toBe(true)

    messageStore.syncMessages.mockResolvedValue({ syncedCount: 0, hasMore: false })
    // Heartbeat stays stale across the reconnect (#1973) — only HEAL_COOLDOWN_MS still
    // gates the second heal; two more realistic ticks clears it.
    await tickStallCadence(pollingStore, 2)
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(2)
  })

  // Issue #1960: sessionStalled reporting — decoupled from the healing action above.
  it('#1960: sessionStalled becomes true once the threshold elapses while sessionConnected stays true', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1960-a', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    expect(pollingStore.sessionStalled).toBe(false)

    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    // syncMessages() deliberately never resolves during this assertion window — the
    // liveness flag is set synchronously before the heal sequence's first await. Assert
    // here (mid-heal) to isolate that synchronous set from whatever the eventual
    // reconnect/poll outcome does to the flag (mirrors the B1 pattern below; see the
    // #1973 tests for reconnect-specific behavior).
    let resolveSync
    vi.spyOn(messageStore, 'syncMessages').mockImplementation(() => new Promise(resolve => { resolveSync = resolve }))

    const healPromise = pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(pollingStore.sessionConnected).toBe(true)

    resolveSync({ syncedCount: 0, hasMore: false })
    apiMock.get.mockResolvedValue({ cursor: 0 })
    await healPromise
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
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1960-e', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    // Advance to just short of STALL_TIMEOUT_MS (40s) via realistic tick cadence (#1974),
    // then take the final threshold-crossing tick manually below.
    await tickStallCadence(pollingStore, 7)
    advanceTime(5000) // final tick — crosses STALL_TIMEOUT_MS (40s)

    // Simulate the exponential-backoff loop having already detected a failure.
    pollingStore.sessionConnected = false
    const syncSpy = vi.spyOn(messageStore, 'syncMessages')

    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(syncSpy).not.toHaveBeenCalled()
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
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1973-a', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
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
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1973-b', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })

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
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1973-c', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
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

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(2)
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
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1974-d', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
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
    expect(messageStore.syncMessages).toHaveBeenCalledWith(sid)
  })

  it('a backgrounded tab does not trigger heal recovery even once genuinely stalled, but keeps reporting the stall', async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1974-e', is_processing: false })
    abortAwareFetchMock()
    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    const syncSpy = vi.spyOn(messageStore, 'syncMessages')

    await pollingStore.connectSession(sid)
    await tickStallCadence(pollingStore, 8) // reaches STALL_TIMEOUT_MS (40s) at normal cadence
    await flush()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(syncSpy).not.toHaveBeenCalled()

    hiddenSpy.mockRestore()
  })

  it("a stale in-flight response from a superseded polling generation can't overwrite the heartbeat/frozen snapshot or clear sessionStalled", async () => {
    const { pollingStore, messageStore, sid } = await setupStallSession({ session_id: 'sess-1974-race', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })

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
})
