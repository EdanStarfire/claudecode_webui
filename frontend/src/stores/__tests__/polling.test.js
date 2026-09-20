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

  it('B1 (#1917): aborts the in-flight fetch synchronously at the start of the heal sequence, before any await', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-b1', is_processing: false })

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
    advanceTime(41000) // past STALL_TIMEOUT_MS (40s)

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
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-inflight', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000) // past STALL_TIMEOUT_MS (40s)

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
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-second-heal', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000)

    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(1)

    // #1973: the reconnect at the end of a heal no longer reseeds the heartbeat — the
    // stale pre-heal value is preserved until a real poll response updates it. This
    // advance still exceeds STALL_TIMEOUT_MS against that stale baseline regardless
    // (and HEAL_COOLDOWN_MS, tracked separately via lastHealedAt, has long since elapsed).
    advanceTime(41000)

    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(2)
    expect(pollingStore.sessionConnected).toBe(true)
  })

  it('#1954: releases the guard after a heal error, allowing a genuine subsequent heal', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-heal-error', is_processing: false })
    abortAwareFetchMock()
    vi.spyOn(messageStore, 'syncMessages').mockRejectedValueOnce(new Error('sync failed'))

    await pollingStore.connectSession(sid)
    advanceTime(41000)

    // syncMessages() rejects, but checkSessionStall() catches it internally (line
    // 316-318-equivalent try/catch) so the heal cycle still reaches its finally block.
    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(1)
    expect(pollingStore.sessionConnected).toBe(true)

    messageStore.syncMessages.mockResolvedValue({ syncedCount: 0, hasMore: false })
    advanceTime(41000)

    await pollingStore.checkSessionStall()
    await flush()

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(2)
  })

  // Issue #1960: sessionStalled reporting — decoupled from the healing action above.
  it('#1960: sessionStalled becomes true once the threshold elapses while sessionConnected stays true', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-1960-a', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    expect(pollingStore.sessionStalled).toBe(false)

    advanceTime(41000) // past STALL_TIMEOUT_MS (40s)

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
    const { pollingStore, sid } = await setup({ session_id: 'sess-1960-b', is_processing: false })

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
    const { pollingStore, sid } = await setup({ session_id: 'sess-1960-c', is_processing: false })

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
    const { pollingStore, sessionStore, sid } = await setup({ session_id: 'sess-1960-d', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000)

    sessionStore.sessions.set(sid, makeSession({ session_id: sid, state: 'paused' }))
    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it('#1960: sessionStalled does not suppress the existing heal-gating early return for #1795', async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-1960-e', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000)

    // Simulate the exponential-backoff loop having already detected a failure.
    pollingStore.sessionConnected = false
    const syncSpy = vi.spyOn(messageStore, 'syncMessages')

    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
    expect(syncSpy).not.toHaveBeenCalled()
  })

  it('#1960: a session switch does not leak sessionStalled=true onto the newly selected session', async () => {
    const { pollingStore, sid } = await setup({ session_id: 'sess-1960-f', is_processing: false })
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
    const { pollingStore, sid } = await setup({ session_id: 'sess-1960-h', is_processing: false })
    abortAwareFetchMock()

    const clearIntervalSpy = vi.spyOn(global, 'clearInterval')
    await pollingStore.connectSession(sid)
    await pollingStore.disconnectSession()

    expect(clearIntervalSpy).toHaveBeenCalled()
  })

  it('#1960: the no-heartbeat branch is reachable and leaves sessionStalled false rather than stale', async () => {
    const { pollingStore, sid } = await setup({ session_id: 'sess-1960-i', is_processing: false })
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
    const { pollingStore, sid } = await setup({ session_id: 'sess-1960-j', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    pollingStore.sessionStalled = true

    pollingStore.resetSessionCursor(sid)

    expect(pollingStore.sessionStalled).toBe(false)
  })

  it("#1973: a heal reconnect that doesn't restore data leaves sessionStalled true", async () => {
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-1973-a', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000) // past STALL_TIMEOUT_MS (40s)

    await pollingStore.checkSessionStall()
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
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-1973-b', is_processing: false })
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
    const { pollingStore, messageStore, sid } = await setup({ session_id: 'sess-1973-c', is_processing: false })
    vi.spyOn(messageStore, 'syncMessages').mockResolvedValue({ syncedCount: 0, hasMore: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000) // past STALL_TIMEOUT_MS (40s) — first heal

    await pollingStore.checkSessionStall()
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    advanceTime(5000) // STALL_CHECK_INTERVAL_MS tick between heals, still no data
    await pollingStore.checkSessionStall()
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    advanceTime(41000) // past STALL_TIMEOUT_MS again (HEAL_COOLDOWN_MS long since elapsed) — second heal
    await pollingStore.checkSessionStall()
    await flush()
    expect(pollingStore.sessionStalled).toBe(true)

    expect(messageStore.syncMessages).toHaveBeenCalledTimes(2)
  })

  it("#1973: baseline is still seeded on a session's first connection", async () => {
    const { pollingStore, sid } = await setup({ session_id: 'sess-1973-d', is_processing: false })
    abortAwareFetchMock()

    await pollingStore.connectSession(sid)
    advanceTime(41000) // measurable only if the baseline was seeded at connect time — regression guard for #1795

    await pollingStore.checkSessionStall()

    expect(pollingStore.sessionStalled).toBe(true)
  })
})
