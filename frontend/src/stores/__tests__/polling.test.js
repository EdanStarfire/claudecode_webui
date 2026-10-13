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
const pushDebugEventMock = vi.hoisted(() => vi.fn())
vi.mock('@/composables/useDebugBuffer', () => ({ pushDebugEvent: pushDebugEventMock }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  triggerReauthMock.mockReset()
  pushDebugEventMock.mockReset()
})

function flush() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

// Epic #1990 stage 5c: the wire shape is now one multiplexed POST /api/poll call
// carrying {timeout, ui: {since}, sessions: {id: {since}}}, answered with
// {ui: {...}, sessions: {...}, backend_status}. `parseBody()` reads back what a
// given fetch() call's JSON body was, for assertions that need to inspect which
// streams/cursors a specific cycle actually requested.
function parseBody(opts) {
  return JSON.parse(opts.body)
}

// Serves each entry of `responses` (a {ui, sessions, backend_status}-shaped body, or a
// function taking (url, opts) for full control) to successive fetch() calls in order;
// once exhausted, every further call hangs until aborted, so the poll loop parks
// instead of spinning once the scripted sequence ends.
function sequencedPollMock(responses) {
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

function hangingAbortAwareFetch(_url, opts) {
  return new Promise((_resolve, reject) => {
    opts?.signal?.addEventListener('abort', () => {
      const err = new Error('Aborted')
      err.name = 'AbortError'
      reject(err)
    })
  })
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

  it('stopUIPolling sets uiState and sessionState to idle (one shared connection, epic #1990 stage 5c)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: {} }])
    pollingStore.startUIPolling()
    await flush()
    expect(pollingStore.uiState).toBe('connected')

    pollingStore.stopUIPolling()

    expect(pollingStore.uiState).toBe('idle')
    expect(pollingStore.sessionState).toBe('idle')
  })

  // Helper: drive a sequence of state_change events (each item is a session state
  // override) through the UI stream of a single multiplexed poll response, then hang
  // until stopUIPolling() aborts the fetch.
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
            ui: {
              events: stateOverridesList.map(overrides => ({
                type: 'state_change',
                data: {
                  session_id: sessionId,
                  session: makeSession({ session_id: sessionId, ...overrides })
                }
              })),
              next_cursor: 1
            },
            sessions: {},
          })
        })
      }
      return hangingAbortAwareFetch(_url, opts)
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

// Epic #1990 stage 5c: ONE shared connection now carries both the UI and every
// subscribed session's stream in a single response's backend_status field — there is
// no longer an independent per-stream value to track. uiBackendStatus/sessionBackendStatus
// are kept as separate exported names purely for API-shape compatibility with existing
// call sites (HeaderRow1.vue, InputArea.vue); they always agree by construction now.
describe('polling store - backendStatus (issue #1989, consolidated epic #1990 stage 5c)', () => {
  it('starts as ok on both exported names', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    expect(pollingStore.uiBackendStatus).toBe('ok')
    expect(pollingStore.sessionBackendStatus).toBe('ok')
  })

  it('a poll response carrying backend_status updates both exported names identically', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: {}, backend_status: 'unreachable' },
    ])

    pollingStore.startUIPolling()
    await flush()
    expect(pollingStore.uiBackendStatus).toBe('unreachable')
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    pollingStore.stopUIPolling()
  })

  it('a poll response carrying backend_status: degraded updates it', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: {}, backend_status: 'degraded' },
    ])

    pollingStore.startUIPolling()
    await flush()
    expect(pollingStore.uiBackendStatus).toBe('degraded')
    pollingStore.stopUIPolling()
  })

  it('a subsequent response carrying ok clears a prior unreachable/degraded state (AC4)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    pollingStore.startUIPolling()
    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: {}, backend_status: 'unreachable' }) })
    await flush()
    expect(pollingStore.uiBackendStatus).toBe('unreachable')

    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 2 }, sessions: {}, backend_status: 'ok' }) })
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.uiBackendStatus).toBe('ok')
  })

  it('a response with the field entirely absent defaults to ok', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()

    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: {} },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(pollingStore.uiBackendStatus).toBe('ok')
  })

  it('a subscribed session\'s own batch arriving in the same response does not need a separate backend_status (one shared field now)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-bs', makeSession({ session_id: 'sess-bs' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { 'sess-bs': { events: [], next_cursor: 0 } }, backend_status: 'unreachable' },
    ])

    await pollingStore.connectSession('sess-bs')
    await flush()
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    expect(pollingStore.uiBackendStatus).toBe('unreachable')
    await pollingStore.disconnectSession()
  })
})

describe('polling store - 401 re-auth (issue #2040)', () => {
  it('startUIPolling: a 401 calls triggerReauth, sets uiState/sessionState idle, and does not schedule a backoff retry', async () => {
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
      await vi.advanceTimersByTimeAsync(16000)

      expect(apiMock.get).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('connectSession (via the shared loop): a 401 calls triggerReauth, sets sessionState idle, and stops the loop', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-401' })

    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.resolve({ detail: 'unauthorized' })
    })

    await pollingStore.connectSession(sid)
    await flush()

    expect(triggerReauthMock).toHaveBeenCalledWith(401)
    expect(pollingStore.sessionState).toBe('idle')

    const callsAfterFirst = fetchSpy.mock.calls.length
    await flush()
    expect(fetchSpy.mock.calls.length).toBe(callsAfterFirst)
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

function rejectOnceThenHangFetchMock() {
  let callCount = 0
  return vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
    callCount++
    if (callCount === 1) {
      return Promise.reject(new Error('Connection refused'))
    }
    return hangingAbortAwareFetch(_url, opts)
  })
}

// Like rejectOnceThenHangFetchMock(), but the second call succeeds with an empty poll
// response — this is what actually confirms a reconnect (retryStaleAppData() only fires
// on a CONFIRMED successful poll). Every call after the second hangs until aborted.
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
        json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: {} }),
      })
    }
    return hangingAbortAwareFetch(_url, opts)
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

      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
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

      uiStore.setAppDataStatus('loaded')
      mockAppDataEndpoints()
      rejectOnceThenHangFetchMock()

      pollingStore.startUIPolling()

      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)

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
      vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Connection refused'))

      pollingStore.startUIPolling()

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

  it('periodic watcher retries a failed app-data load even when /api/poll itself never errors (AC7)', async () => {
    vi.useFakeTimers()
    try {
      const { usePollingStore } = await import('@/stores/polling')
      const { useUIStore } = await import('@/stores/ui')
      const pollingStore = usePollingStore()
      const uiStore = useUIStore()

      uiStore.setAppDataStatus('failed')
      mockAppDataEndpoints()
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => hangingAbortAwareFetch(_url, opts))

      pollingStore.startUIPolling()
      await vi.advanceTimersByTimeAsync(0)

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
      apiMock.get.mockRejectedValue(new Error('still down'))
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => hangingAbortAwareFetch(_url, opts))

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

    mockAppDataEndpoints()

    sequencedPollMock([
      {
        ui: {
          events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }],
          next_cursor: 5,
        },
        sessions: {},
      },
    ])

    pollingStore.startUIPolling()
    for (let i = 0; i < 20; i++) {
      if (!messageStore.messagesBySession.has(sid)) break
      await flush()
    }
    pollingStore.stopUIPolling()

    expect(sessionStore.sessions.has(sid)).toBe(false)
    expect(messageStore.messagesBySession.has(sid)).toBe(false)
    expect(sessionStore.currentSessionId).toBeNull()
  })

  it('ui.evicted triggers loadAppData() and does not dispatch the carried events', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints()
    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedPollMock([
      {
        ui: {
          events: [{ type: 'rate_limits_update', data: { requests_remaining: 999 } }],
          next_cursor: 5,
          evicted: true,
        },
        sessions: {},
      },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(apiMock.get).toHaveBeenCalledWith('/api/projects')
    expect(apiMock.get).toHaveBeenCalledWith('/api/sessions')
    expect(setRateLimitsSpy).not.toHaveBeenCalledWith({ requests_remaining: 999 })
  })

  it('mid-batch stream_reset on the UI stream: events before the marker are dispatched normally, the marker and anything after trigger the resync', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    mockAppDataEndpoints()
    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedPollMock([
      {
        ui: {
          events: [
            { type: 'rate_limits_update', data: { requests_remaining: 111 } },
            { type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } },
            { type: 'rate_limits_update', data: { requests_remaining: 999 } },
          ],
          next_cursor: 5,
        },
        sessions: {},
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

  it('a normal response with no evicted/reset still dispatches its UI events as before', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useUIStore } = await import('@/stores/ui')
    const pollingStore = usePollingStore()
    const uiStore = useUIStore()

    const setRateLimitsSpy = vi.spyOn(uiStore, 'setRateLimits')

    sequencedPollMock([
      { ui: { events: [{ type: 'rate_limits_update', data: { requests_remaining: 42 } }], next_cursor: 1 }, sessions: {} },
    ])

    pollingStore.startUIPolling()
    await flush()
    pollingStore.stopUIPolling()

    expect(setRateLimitsSpy).toHaveBeenCalledWith({ requests_remaining: 42 })
    expect(apiMock.get).not.toHaveBeenCalledWith('/api/projects')
  })
})

// ============================================================================
// Epic #1990 stage 5c: the N-task/two-connection design collapses into ONE shared
// StreamConnection — uiState/sessionState (mod the archived override) now agree by
// construction. The describe blocks below exercise the merged transition behavior.
// ============================================================================

describe('polling store - StreamConnection transition table (consolidated, epic #1990 stage 5c)', () => {
  it('idle -> connect() -> connecting, immediately on connectSession()', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-connecting' })
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => hangingAbortAwareFetch(_url, opts))

    await pollingStore.connectSession(sid)

    expect(pollingStore.sessionState).toBe('connecting')
    expect(pollingStore.uiState).toBe('connecting')
    expect(pollingStore.sessionRetryCount).toBe(0)
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('connecting + poll OK normal batch -> connected, retryCount resets to 0, hasEverSeenResponse flips true', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-connected' })
    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }])

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionRetryCount).toBe(0)
    expect(pollingStore.sessionHasEverConnected).toBe(true)
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('connected + poll OK but backend_status unreachable -> unreachable (AC2: backend_status decides it, literally)', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-unreachable' })
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)
    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')

    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 0 } }, backend_status: 'unreachable' }) })
    await flush()

    expect(pollingStore.sessionState).toBe('unreachable')
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('connected + poll OK with backend_status degraded stays connected (decision 1: degraded is a non-overriding annotation, not a state)', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-degraded' })
    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } }, backend_status: 'degraded' }])

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionBackendStatus).toBe('degraded')
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('connected + poll OK, the CURRENT session\'s batch contains stream_reset -> resyncing, then connected once the resync resolves', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-t1-resync' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid
    let resolveLoad
    vi.spyOn(messageStore, 'loadMessages').mockImplementation(() => new Promise(resolve => { resolveLoad = resolve }))

    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 } } },
    ])

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('resyncing')

    resolveLoad()
    await flush()

    expect(pollingStore.sessionState).toBe('connected')
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('connecting/connected/resyncing/unreachable + fetch throws (non-abort) -> backoff, retryCount++', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-backoff' })
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Connection refused'))

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('backoff')
    expect(pollingStore.sessionRetryCount).toBe(1)
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('backoff + timer fires -> connecting (retry fetch)', async () => {
    vi.useFakeTimers()
    try {
      const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t1-backoff-retry' })
      let callCount = 0
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
        callCount++
        if (callCount === 1) return Promise.reject(new Error('Connection refused'))
        return hangingAbortAwareFetch(_url, opts)
      })

      await pollingStore.connectSession(sid)
      await vi.advanceTimersByTimeAsync(0)
      expect(pollingStore.sessionState).toBe('backoff')

      await vi.advanceTimersByTimeAsync(2000)
      expect(pollingStore.sessionState).toBe('connecting')
      expect(callCount).toBe(2)

      await pollingStore.disconnectSession()
      pollingStore.stopUIPolling()
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
      return hangingAbortAwareFetch(_url, opts)
    })

    await pollingStore.connectSession(sid)
    await flush()

    expect(pollingStore.sessionState).toBe('connecting')
    expect(pollingStore.sessionRetryCount).toBe(0)
    expect(callCount).toBe(2)
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('resyncing + resync (loadMessages) throws -> backoff, same path as any other fetch error (no second retry mechanism)', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-t1-resync-fail' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid
    vi.spyOn(messageStore, 'loadMessages').mockRejectedValue(new Error('history fetch failed'))

    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 } } },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(pollingStore.sessionState).toBe('backoff')
    expect(pollingStore.sessionRetryCount).toBe(1)
    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('UI stream: connecting + poll OK -> connected; batch with stream_reset -> resyncing -> connected', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore()
    mockAppDataEndpoints()

    let resolveLoad
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore()
    vi.spyOn(sessionStore, 'fetchSessions').mockImplementation(() => new Promise(resolve => { resolveLoad = resolve }))

    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    pollingStore.startUIPolling()
    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: {} }) })
    await flush()
    expect(pollingStore.uiState).toBe('connected')

    resolveFetch({
      ok: true,
      json: () => Promise.resolve({
        ui: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
        sessions: {},
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

// T2 (plan's Testing Strategy): proves the ABSENCE of any wall-clock staleness mechanism.
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

      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(fetchCallCount).toBe(1)
      expect(pollingStore.sessionState).toBe('connecting')

      resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 7 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }) })
      await vi.advanceTimersByTimeAsync(0)

      expect(pollingStore.sessionState).toBe('connected')
      expect(pollingStore.sessionRetryCount).toBe(0)

      await pollingStore.disconnectSession()
      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('polling store - backend_status ok -> unreachable -> ok scenario (T3, issue #2129 stage 5b)', () => {
  it('the shared connection tracks the full ok -> unreachable -> ok sequence correctly', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-t3' })
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)
    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } }, backend_status: 'ok' }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionBackendStatus).toBe('ok')

    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 0 } }, backend_status: 'unreachable' }) })
    await flush()
    expect(pollingStore.sessionState).toBe('unreachable')
    expect(pollingStore.sessionBackendStatus).toBe('unreachable')
    expect(pollingStore.sessionHasEverConnected).toBe(true)

    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 3 }, sessions: { [sid]: { events: [], next_cursor: 0 } }, backend_status: 'ok' }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')
    expect(pollingStore.sessionBackendStatus).toBe('ok')

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })
})

// Dedicated risk test: the evicted/reset cursor-adoption handoff via loadedEventCursors
// must transfer byte-for-byte into the new subscription-map design.
describe('polling store - dedicated risk: evicted/reset resync cursor fidelity (epic #1990 stage 5c)', () => {
  it('adopts the cursor from loadedEventCursors (not next_cursor) once the resync completes', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-risk-cursor' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid

    vi.spyOn(messageStore, 'loadMessages').mockImplementation(async (sessionId) => {
      messageStore.loadedEventCursors.set(sessionId, 999)
    })

    const fetchMock = sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 } } },
      { ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 6 } } },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(messageStore.loadedEventCursors.has(sid)).toBe(false)
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(parseBody(fetchMock.mock.calls[1][1]).sessions[sid].since).toBe(999)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('falls back to next_cursor when loadMessages() does not set loadedEventCursors', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-risk-cursor-fallback' })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore()
    sessionStore.currentSessionId = sid
    vi.spyOn(messageStore, 'loadMessages').mockResolvedValue(undefined)

    const fetchMock = sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 } } },
      { ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 6 } } },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(parseBody(fetchMock.mock.calls[1][1]).sessions[sid].since).toBe(5)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('an evicted session that vanishes from the UI resync still gets full session_deleted cleanup (carried over from #2065 AC7)', async () => {
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

    sequencedPollMock([
      { ui: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 }, sessions: {} },
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

// Epic #1990 stage 5c structural note: the old "session_reset arriving mid-resync" risk
// test relied on TWO independent concurrent poll loops (UI, session) racing each other —
// that race can no longer occur by construction, since there is only one combined fetch
// cycle now and UI/session batches within it are processed sequentially, not concurrently.
// The new equivalent risk is ORDERING within one response: a UI session_reset event and
// that same session's own stream_reset arriving in the SAME multiplexed batch.
describe('polling store - dedicated risk: session_reset ordering within one multiplexed batch (epic #1990 stage 5c)', () => {
  it('a UI session_reset for the session whose OWN batch in the same response also carries a stream_reset: eviction wins cleanly, no stale resync is attempted', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-risk-reset-order' })
    const { useMessageStore } = await import('@/stores/message')
    const { useResourceStore } = await import('@/stores/resource')
    const messageStore = useMessageStore()
    const resourceStore = useResourceStore()
    sessionStore.currentSessionId = sid

    const loadMessagesSpy = vi.spyOn(messageStore, 'loadMessages').mockResolvedValue(undefined)
    const clearMessagesSpy = vi.spyOn(messageStore, 'clearMessages')
    const clearResourcesSpy = vi.spyOn(resourceStore, 'clearResources')

    sequencedPollMock([
      {
        // UI stream's session_reset for sid is processed FIRST (ui before sessions in
        // the loop), evicting sid's subscription before sid's own batch (also in this
        // same response) is ever looked at.
        ui: { events: [{ type: 'session_reset', data: { session_id: sid } }], next_cursor: 1 },
        sessions: { [sid]: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 } },
      },
    ])

    await pollingStore.connectSession(sid)
    await flush()

    expect(clearMessagesSpy).toHaveBeenCalledWith(sid)
    expect(clearResourcesSpy).toHaveBeenCalledWith(sid)
    // The session's own batch bailed out immediately (subscription already evicted by
    // the UI handler moments earlier in the same cycle) — no resync was attempted for
    // data that's about to be cleared anyway.
    expect(loadMessagesSpy).not.toHaveBeenCalled()

    pollingStore.stopUIPolling()
  })
})

// Epic #1990 stage 5c structural note: the old "session-switch-mid-fetch race" test relied
// on each session having its OWN independent fetch — now there is one shared in-flight
// request covering everything, and touchSubscription() interrupts it outright (not just
// "the old session's slice") when a genuinely new stream is added.
describe('polling store - dedicated risk: subscribing a new session interrupts a stale in-flight cycle (epic #1990 stage 5c)', () => {
  it('connectSession for a brand-new session aborts the in-flight request and restarts with the updated subscription, rather than waiting out the long-poll timeout', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-old', makeSession({ session_id: 'sess-old' }))
    sessionStore.sessions.set('sess-new', makeSession({ session_id: 'sess-new' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    const abortedUrls = []
    let fetchCallCount = 0
    vi.spyOn(global, 'fetch').mockImplementation((url, opts) => {
      fetchCallCount++
      if (fetchCallCount === 1) {
        // The first cycle (ui + sess-old only) — left pending so the switch below
        // interrupts it instead of it resolving naturally.
        return new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () => {
            abortedUrls.push(JSON.parse(opts.body))
            const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
          })
        })
      }
      // Every subsequent cycle hangs too — only the abort-vs-not distinction matters here.
      return hangingAbortAwareFetch(url, opts)
    })

    await pollingStore.connectSession('sess-old')
    expect(fetchCallCount).toBe(1)

    await pollingStore.connectSession('sess-new')

    expect(pollingStore.currentSessionId).toBe('sess-new')
    // The stale first request was aborted (not left to resolve naturally) and a second
    // cycle was issued immediately, carrying the updated subscription set.
    expect(fetchCallCount).toBe(2)
    expect(abortedUrls).toHaveLength(1)
    expect(abortedUrls[0].sessions).toHaveProperty('sess-old')

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })
})

// Epic #1990 stage 5c (US2/US3): the subscription-set model replaces sessionCursors'
// passive cache — every entry is now ACTIVELY polled, so a recently-viewed-but-not-current
// session keeps receiving live events the whole time it's subscribed, and switching back to
// it resumes from its own live cursor (not a fresh bootstrap).
describe('polling store - subscription-set cursor continuity (US2, epic #1990 stage 5c)', () => {
  it('switching A -> B -> back to A resumes A from its own subscription cursor, not a fresh "cursor right now" bootstrap', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()

    sessionStore.sessions.set('sess-a', makeSession({ session_id: 'sess-a' }))
    sessionStore.sessions.set('sess-b', makeSession({ session_id: 'sess-b' }))
    messageStore.messagesBySession.set('sess-a', [{ id: 'm1', type: 'user' }])
    apiMock.get.mockResolvedValue({ cursor: 0 }) // only hit for a genuinely first-time connect

    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-a': { events: [], next_cursor: 123 } } }])
    await pollingStore.connectSession('sess-a')
    await flush()

    sequencedPollMock([{ ui: { events: [], next_cursor: 2 }, sessions: { 'sess-a': { events: [], next_cursor: 123 }, 'sess-b': { events: [], next_cursor: 1 } } }])
    await pollingStore.connectSession('sess-b')
    await flush()

    apiMock.get.mockClear()
    const fetchMock = vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(() => {}))

    await pollingStore.connectSession('sess-a')

    // Resumed from A's own subscription cursor (123, kept live from A's own first poll
    // cycle above) — NOT a fresh bootstrap GET (which would only happen if A had aged
    // out of the LRU) and not from cursor 0.
    expect(apiMock.get).not.toHaveBeenCalledWith('/api/poll/session/sess-a/cursor')
    const lastBody = parseBody(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1])
    expect(lastBody.sessions['sess-a'].since).toBe(123)

    pollingStore.stopUIPolling()
  })

  it('resetSessionCursor() evicts the named session entirely, forcing its next connect to start fresh', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-reset', makeSession({ session_id: 'sess-reset' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-reset': { events: [], next_cursor: 55 } } }])
    await pollingStore.connectSession('sess-reset')
    await flush()
    await pollingStore.disconnectSession()
    pollingStore.resetSessionCursor('sess-reset')

    apiMock.get.mockClear()
    const fetchMock = vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(() => {}))

    await pollingStore.connectSession('sess-reset')

    expect(apiMock.get).toHaveBeenCalledWith('/api/poll/session/sess-reset/cursor')
    const lastBody = parseBody(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1])
    expect(lastBody.sessions['sess-reset'].since).toBe(0)

    pollingStore.stopUIPolling()
  })

  it('cleanupSessionPollingState() evicts a deleted (non-current) session\'s subscription too', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-cleanup', makeSession({ session_id: 'sess-cleanup' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-cleanup': { events: [], next_cursor: 77 } } }])
    await pollingStore.connectSession('sess-cleanup')
    await flush()
    await pollingStore.disconnectSession()

    pollingStore.cleanupSessionPollingState('sess-cleanup')

    apiMock.get.mockClear()
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(() => {}))
    await pollingStore.connectSession('sess-cleanup')

    expect(apiMock.get).toHaveBeenCalledWith('/api/poll/session/sess-cleanup/cursor')
    pollingStore.stopUIPolling()
  })

  it('disconnectSession() evicts the session entirely — unlike normal navigation away, which keeps it live in the background', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-explicit-disc', makeSession({ session_id: 'sess-explicit-disc' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-explicit-disc': { events: [], next_cursor: 0 } } }])
    await pollingStore.connectSession('sess-explicit-disc')
    await flush()
    expect(pollingStore.sessionHasEverConnected).toBe(true)

    await pollingStore.disconnectSession()

    expect(pollingStore.currentSessionId).toBeNull()
    // sessionHasEverConnected is now about whatever currentSessionId is (null) — not
    // meaningful once there's no current session, but exercised here for completeness.
    expect(pollingStore.sessionHasEverConnected).toBe(false)

    pollingStore.stopUIPolling()
  })
})

// US2's core claim: a recently-viewed, still-subscribed (but not current) session's events
// are applied LIVE — not just cached — so switching back shows everything that happened
// while it was in the background, with no reload/resync needed.
describe('polling store - background live sessions (US2, epic #1990 stage 5c AC1/AC2)', () => {
  it('dispatchEvent applies a background (non-current) subscribed session\'s events live, not just the current session\'s', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()

    sessionStore.sessions.set('sess-fg', makeSession({ session_id: 'sess-fg' }))
    sessionStore.sessions.set('sess-bg', makeSession({ session_id: 'sess-bg' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    // Subscribe to both (bg first, then switch to fg as current — bg stays subscribed,
    // same LRU-background pattern US2 is about).
    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-bg': { events: [], next_cursor: 0 } } }])
    await pollingStore.connectSession('sess-bg')
    await flush()

    sequencedPollMock([{
      ui: { events: [], next_cursor: 2 },
      sessions: {
        'sess-fg': { events: [], next_cursor: 0 },
        // sess-bg is still subscribed (within the LRU) and gets a LIVE message while
        // the user is looking at sess-fg instead.
        'sess-bg': { events: [{ type: 'message', data: { type: 'assistant', id: 'bg-m1', text: 'hi' } }], next_cursor: 1 },
      },
    }])
    await pollingStore.connectSession('sess-fg')
    await flush()

    // Applied live via message.js's applyRecord(), with no loadMessages()/resync needed —
    // this is the whole point of US2 over the old passive-cache model.
    const bgMessages = messageStore.messagesBySession.get('sess-bg') || []
    expect(bgMessages.some(m => m.id === 'bg-m1')).toBe(true)
    expect(pollingStore.currentSessionId).toBe('sess-fg')

    pollingStore.stopUIPolling()
  })

  it('a batch for a session that is no longer subscribed is ignored, not applied (subscription-set gate)', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()

    sessionStore.sessions.set('sess-evicted-late', makeSession({ session_id: 'sess-evicted-late' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    // Subscribe, then explicitly evict (disconnectSession's full-eviction semantics) —
    // a real server would stop being asked about this session on the NEXT request, but
    // a late-arriving response for a cycle built before the eviction is exactly the
    // scenario _processSessionBatch()'s own `subscriptions.get(sessionId)` guard exists
    // for: defense against exactly this ordering, not just a theoretical case.
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))
    await pollingStore.connectSession('sess-evicted-late')
    await pollingStore.disconnectSession()

    resolveFetch({
      ok: true,
      json: () => Promise.resolve({
        ui: { events: [], next_cursor: 1 },
        sessions: { 'sess-evicted-late': { events: [{ type: 'message', data: { type: 'assistant', id: 'x', text: 'hi' } }], next_cursor: 1 } },
      }),
    })
    await flush()

    expect(messageStore.messagesBySession.has('sess-evicted-late')).toBe(false)
    pollingStore.stopUIPolling()
  })
})

// US3: LRU-bounded subscription set — once more than MAX_SUBSCRIBED_SESSIONS (5) sessions
// have been viewed, the least-recently-touched ones age out of the subscription set
// entirely (bookkeeping released), while the most recent 5 stay live.
describe('polling store - LRU subscription-set eviction (US3, epic #1990 stage 5c)', () => {
  it('viewing a 6th session evicts the least-recently-viewed one from the subscription set', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    apiMock.get.mockResolvedValue({ cursor: 0 })

    for (let i = 1; i <= 6; i++) {
      sessionStore.sessions.set(`sess-lru-${i}`, makeSession({ session_id: `sess-lru-${i}` }))
    }

    // Must be abort-aware: each new connectSession() call interrupts the previous
    // cycle's in-flight request (interruptIfStale()), and the loop can only progress
    // to pick up the fresh subscription if that in-flight promise actually rejects on
    // abort rather than hanging forever regardless.
    vi.spyOn(global, 'fetch').mockImplementation(hangingAbortAwareFetch)

    for (let i = 1; i <= 6; i++) {
      await pollingStore.connectSession(`sess-lru-${i}`)
      await flush()
    }

    const fetchMock = vi.spyOn(global, 'fetch')
    const lastBody = parseBody(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1])
    // sess-lru-1 (touched first, longest ago) aged out; the 5 most recent (2-6) remain.
    expect(lastBody.sessions).not.toHaveProperty('sess-lru-1')
    for (let i = 2; i <= 6; i++) {
      expect(lastBody.sessions).toHaveProperty(`sess-lru-${i}`)
    }

    pollingStore.stopUIPolling()
  })

  it('re-touching an already-subscribed session refreshes its LRU position instead of letting it age out', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    apiMock.get.mockResolvedValue({ cursor: 0 })

    for (let i = 1; i <= 5; i++) {
      sessionStore.sessions.set(`sess-refresh-${i}`, makeSession({ session_id: `sess-refresh-${i}` }))
    }
    sessionStore.sessions.set('sess-refresh-6', makeSession({ session_id: 'sess-refresh-6' }))

    vi.spyOn(global, 'fetch').mockImplementation(hangingAbortAwareFetch)

    for (let i = 1; i <= 5; i++) {
      await pollingStore.connectSession(`sess-refresh-${i}`)
      await flush()
    }
    // Re-touch sess-refresh-1 (the oldest) so it's no longer the LRU candidate.
    await pollingStore.connectSession('sess-refresh-1')
    await flush()
    // Now a 6th, genuinely new session — sess-refresh-2 (now the actual oldest) should
    // age out instead of sess-refresh-1 (just refreshed).
    await pollingStore.connectSession('sess-refresh-6')
    await flush()

    const fetchMock = vi.spyOn(global, 'fetch')
    const lastBody = parseBody(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1])
    expect(lastBody.sessions).toHaveProperty('sess-refresh-1')
    expect(lastBody.sessions).not.toHaveProperty('sess-refresh-2')

    pollingStore.stopUIPolling()
  })

  // WebUI-Agent review finding (stage 5c, phase 2 checkpoint): the SAME cancel-and-restart
  // interruption mechanism verified not to thrash on the relay side (interruptIfStale())
  // needs the same verification on the browser's own single poll loop — rapid back-to-back
  // connectSession() calls for brand-new sessions must still converge to one completed
  // cycle covering everything, not re-cancel indefinitely.
  it('rapid connectSession() calls for many brand-new sessions do not starve the loop', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()
    apiMock.get.mockResolvedValue({ cursor: 0 })

    const sessionIds = Array.from({ length: 5 }, (_, i) => `sess-churn-${i}`)
    for (const sid of sessionIds) {
      sessionStore.sessions.set(sid, makeSession({ session_id: sid }))
    }

    const completedCycleSessionKeys = []
    vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
      // A real (if tiny) round trip, unlike an instantly-resolving mock — needed so an
      // in-flight call actually has a window in which a rapid-fire touch can interrupt
      // it, mirroring the equivalent backend regression test's rationale exactly.
      return new Promise((resolve, reject) => {
        const body = parseBody(opts)
        const timer = setTimeout(() => {
          completedCycleSessionKeys.push(Object.keys(body.sessions))
          resolve({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: {} }) })
        }, 20)
        opts?.signal?.addEventListener('abort', () => {
          clearTimeout(timer)
          const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
        })
      })
    })

    // Rapid-fire connect of 5 distinct brand-new sessions, no await of a full cycle in
    // between (only each connectSession()'s own cursor-bootstrap microtask) — each is
    // "new", so each triggers interruptIfStale() against whatever the loop is currently
    // doing.
    for (const sid of sessionIds) {
      await pollingStore.connectSession(sid)
    }

    const converged = await new Promise(resolve => {
      const deadline = Date.now() + 2000
      const check = () => {
        const last = completedCycleSessionKeys[completedCycleSessionKeys.length - 1]
        if (last && sessionIds.every(sid => last.includes(sid))) {
          resolve(true)
        } else if (Date.now() > deadline) {
          resolve(false)
        } else {
          setTimeout(check, 10)
        }
      }
      check()
    })

    expect(converged).toBe(true)
    expect(completedCycleSessionKeys.length).toBeLessThan(50)

    pollingStore.stopUIPolling()
  })
})

// AC3/UC3, decision 5: an archive/no-live-stream session view marks the session view
// explicitly archived instead of leaving it an implicit, uninspectable absence.
describe('polling store - markSessionArchived (issue #2129 stage 5b, AC3/AC4; epic #1990 stage 5c)', () => {
  it('sets currentSessionId and sessionState to archived without starting any poll loop or subscribing the session', async () => {
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
    sequencedPollMock([{ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-live': { events: [], next_cursor: 0 } } }])

    pollingStore.markSessionArchived('sess-archived-2')
    expect(pollingStore.sessionState).toBe('archived')

    await pollingStore.connectSession('sess-live')
    await flush()

    expect(pollingStore.currentSessionId).toBe('sess-live')
    expect(pollingStore.sessionState).toBe('connected')

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  // Epic #1990 stage 5c: `isCurrentViewArchived` is now a plain, independently-owned ref —
  // unlike stage 5b's per-connection 'archived' STATE (which a stale in-flight poll response
  // could overwrite back to 'connected'), nothing in the poll loop ever writes this ref, so
  // this guarantee now holds by construction rather than needing an explicit disconnect-
  // before-mark dance.
  it('marking the currently-connected session itself archived is never reverted by a stale in-flight poll response, by construction', async () => {
    const { pollingStore, sessionStore, sid } = await setupPollSession({ session_id: 'sess-archive-self' })
    sessionStore.currentSessionId = sid

    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)
    resolveFetch({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }) })
    await flush()
    expect(pollingStore.sessionState).toBe('connected')

    const staleResolve = resolveFetch

    pollingStore.markSessionArchived(sid)
    expect(pollingStore.sessionState).toBe('archived')

    staleResolve({ ok: true, json: () => Promise.resolve({ ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }) })
    await flush()

    expect(pollingStore.sessionState).toBe('archived')
    expect(pollingStore.currentSessionId).toBe(sid)

    pollingStore.stopUIPolling()
  })
})

// Issue #2161: input stayed gated behind a "reconnecting" banner for up to 30s after
// stage 5c (#2130), because every poll cycle — including the very first one — asked for
// a full 30s long-poll. The fix is a one-shot client-side fast-probe flag: exactly the
// next request built after a fresh connect() or a subscription-set change sends
// timeout:0 instead of timeout:30, then reverts. The one-shot discipline is the single
// point of failure for this mechanism (a flag that fails to clear degrades every poll
// cycle into a 0-timeout busy-poll, worse than the bug being fixed) — hence the dedicated
// THIRD-cycle assertions below, not just first-vs-second.
describe('polling store - fast-probe one-shot timeout (issue #2161)', () => {
  it('T1 (AC1/AC2/AC3): the first request after connect sends timeout:0 and reaches connected off that single response', async () => {
    vi.useFakeTimers()
    try {
      const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-fp-t1' })
      const fetchMock = sequencedPollMock([
        { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } },
      ])

      await pollingStore.connectSession(sid)
      // Deliberately NOT advancing by anything near 30s — a regression that silently
      // reintroduces the 30s dependency must fail by never reaching 'connected' here,
      // not just run slow.
      await vi.advanceTimersByTimeAsync(0)

      expect(parseBody(fetchMock.mock.calls[0][1]).timeout).toBe(0)
      expect(pollingStore.sessionHasEverConnected).toBe(true)
      expect(pollingStore.uiState).toBe('connected')

      await pollingStore.disconnectSession()
      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })

  it('one-shot regression: the SECOND request reverts to timeout:30, and a THIRD (unchanged) cycle stays at 30 — not an off-by-one', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-fp-oneshot' })
    const fetchMock = sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } },
      { ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 0 } } },
      { ui: { events: [], next_cursor: 3 }, sessions: { [sid]: { events: [], next_cursor: 0 } } },
    ])

    await pollingStore.connectSession(sid)
    await flush()
    await flush()
    await flush()

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3)
    expect(parseBody(fetchMock.mock.calls[0][1]).timeout).toBe(0)
    expect(parseBody(fetchMock.mock.calls[1][1]).timeout).toBe(30)
    expect(parseBody(fetchMock.mock.calls[2][1]).timeout).toBe(30)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('T2: a subscription-set change mid-flight interrupts the in-flight request; the REPLACEMENT request sends timeout:0 and a THIRD (unchanged) cycle reverts to 30', async () => {
    const { usePollingStore } = await import('@/stores/polling')
    const { useSessionStore } = await import('@/stores/session')
    const pollingStore = usePollingStore()
    const sessionStore = useSessionStore()

    sessionStore.sessions.set('sess-fp-old', makeSession({ session_id: 'sess-fp-old' }))
    sessionStore.sessions.set('sess-fp-new', makeSession({ session_id: 'sess-fp-new' }))
    apiMock.get.mockResolvedValue({ cursor: 0 })

    let fetchCallCount = 0
    const bodies = []
    vi.spyOn(global, 'fetch').mockImplementation((url, opts) => {
      fetchCallCount++
      bodies.push(parseBody(opts))
      if (fetchCallCount === 1) {
        // First cycle (ui + sess-fp-old) left pending so the switch below interrupts it
        // instead of it resolving naturally.
        return new Promise((_resolve, reject) => {
          opts?.signal?.addEventListener('abort', () => {
            const err = new Error('Aborted'); err.name = 'AbortError'; reject(err)
          })
        })
      }
      if (fetchCallCount === 2) {
        // The replacement request (carrying the new subscription) resolves normally.
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: { 'sess-fp-new': { events: [], next_cursor: 0 } } }),
        })
      }
      // Third cycle onward hangs — only the timeout value matters from here.
      return hangingAbortAwareFetch(url, opts)
    })

    await pollingStore.connectSession('sess-fp-old')
    expect(fetchCallCount).toBe(1)
    expect(bodies[0].timeout).toBe(0) // the very first connect is fast-probed too (AC1)

    await pollingStore.connectSession('sess-fp-new')
    await flush()

    expect(fetchCallCount).toBe(3)
    expect(bodies[1].timeout).toBe(0)
    expect(bodies[1].sessions).toHaveProperty('sess-fp-new')
    expect(bodies[2].timeout).toBe(30)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })
})

// Issue #2161 (AC4): `conn.hasEverConnected` already tracked exactly this signal — it
// was unused dead state before this fix. Exposed as `transportHasEverConnected` so
// InputArea.vue can distinguish a genuine first connection from a reconnect after a
// previously-successful connection was lost.
describe('polling store - transportHasEverConnected (AC4, issue #2161)', () => {
  it('is false immediately after connect()/before the first response, true after the first connected transition, and resets to false on disconnect()', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-ac4' })
    let resolveFetch
    vi.spyOn(global, 'fetch').mockImplementation(() => new Promise(resolve => { resolveFetch = resolve }))

    await pollingStore.connectSession(sid)
    expect(pollingStore.transportHasEverConnected).toBe(false)

    resolveFetch({
      ok: true,
      json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }),
    })
    await flush()
    expect(pollingStore.transportHasEverConnected).toBe(true)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
    expect(pollingStore.transportHasEverConnected).toBe(false)
  })
})

// Issue #2161 (AC5): transition() used to call pushDebugEvent() unconditionally, logging
// a 'connected -> connected' no-op on every single idle poll cycle. Only an actual state
// change should be debug-worthy.
describe('polling store - debug-event noise guard on no-op transitions (AC5, issue #2161)', () => {
  it('does not log a state-transition debug event for a connected -> connected no-op cycle, but does for the genuine connecting -> connected transition', async () => {
    const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-ac5' })
    sequencedPollMock([
      { ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } },
      { ui: { events: [], next_cursor: 2 }, sessions: { [sid]: { events: [], next_cursor: 0 } } },
    ])

    await pollingStore.connectSession(sid)
    await flush()

    const transitionsToConnected = pushDebugEventMock.mock.calls.filter(
      ([, tag, data]) => tag === 'state-transition' && data.to === 'connected'
    )
    expect(transitionsToConnected).toHaveLength(1) // the genuine connecting -> connected transition

    pushDebugEventMock.mockClear()
    await flush() // second (connected -> connected no-op) cycle

    const noOpTransitions = pushDebugEventMock.mock.calls.filter(
      ([, tag, data]) => tag === 'state-transition' && data.to === 'connected'
    )
    expect(noOpTransitions).toHaveLength(0)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()
  })

  it('still logs a state-transition debug event across a genuine connected -> backoff -> connecting -> connected cycle', async () => {
    vi.useFakeTimers()
    try {
      const { pollingStore, sid } = await setupPollSession({ session_id: 'sess-ac5-cycle' })
      let callCount = 0
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
        callCount++
        if (callCount === 1) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ ui: { events: [], next_cursor: 1 }, sessions: { [sid]: { events: [], next_cursor: 0 } } }),
          })
        }
        if (callCount === 2) {
          return Promise.reject(new Error('Connection refused'))
        }
        return hangingAbortAwareFetch(_url, opts)
      })

      await pollingStore.connectSession(sid)
      await vi.advanceTimersByTimeAsync(0) // cycle 1: connecting -> connected
      await vi.advanceTimersByTimeAsync(0) // cycle 2: fetch throws -> connected -> backoff
      await vi.advanceTimersByTimeAsync(2000) // backoff timer fires -> connecting (retry)

      const transitions = pushDebugEventMock.mock.calls
        .filter(([, tag]) => tag === 'state-transition')
        .map(([, , data]) => `${data.from}->${data.to}`)

      expect(transitions.filter(t => t === 'connecting->connected')).toHaveLength(1)
      expect(transitions).not.toContain('connected->connected')
      expect(transitions).toContain('connected->backoff')
      expect(transitions).toContain('backoff->connecting')

      await pollingStore.disconnectSession()
      pollingStore.stopUIPolling()
    } finally {
      vi.useRealTimers()
    }
  })
})
