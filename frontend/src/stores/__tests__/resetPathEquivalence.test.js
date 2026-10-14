import { describe, it, expect, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeSession, makeProject } from '@/test-utils/factories'
import {
  listRawFixtureNames,
  loadRawFixture,
  normalizeForComparison,
} from './helpers/fixtureEquivalence'

// Issue #2160 (AC3, stage-5-residuals hardening): equivalence.test.js proves the live
// event path and the REST reload path converge on identical content for a FULL,
// uninterrupted history load. polling.test.js proves the StreamConnection state
// machine's transitions are correct against synthetic, hand-built payloads. Neither
// combines the two: real recorded fixture data driven through the ACTUAL fault-recovery
// paths (session-stream stream_reset/evicted resync, UI-stream stream_reset "Backend
// restart", and a transient unreachable/backoff stall) — this file fills that gap.
//
// Only the FIRST fixture (listRawFixtureNames()[0]) is used — this is a transport/
// state-machine correctness check, not a message-pipeline content check (every fixture
// is already covered for that by equivalence.test.js). One fixture's worth of real
// payload is enough to drive the state machine through each fault; mirrors
// src/tests/test_fault_simulation.py's own single-sample-payload (_SAMPLE_EVENTS)
// reasoning on the Python side.
//
// Per this file's own brief: duplicates small helpers locally from equivalence.test.js
// and polling.test.js (sequencedPollMock, hangingAbortAwareFetch, flush,
// applyStoredTaskLifecycleFrames, a trimmed replayRestPath) rather than importing
// across test files.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn().mockReturnValue(null),
  triggerReauth: vi.fn(),
}))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
// polling.js calls pushDebugEvent() on every reset/evicted event (and on every genuine
// state transition) — unmocked, this hits whatever the real useDebugBuffer composable
// backs onto outside a test environment and is not something this suite wants to assert
// on, so it's stubbed exactly like polling.test.js stubs it.
const pushDebugEventMock = vi.hoisted(() => vi.fn())
vi.mock('@/composables/useDebugBuffer', () => ({ pushDebugEvent: pushDebugEventMock }))

function flush() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

// Duplicated from polling.test.js's own helper of the same name/shape.
function hangingAbortAwareFetch(_url, opts) {
  return new Promise((_resolve, reject) => {
    opts?.signal?.addEventListener('abort', () => {
      const err = new Error('Aborted')
      err.name = 'AbortError'
      reject(err)
    })
  })
}

// Duplicated from polling.test.js's own helper of the same name/shape: serves each
// entry of `responses` to successive fetch() calls in order; once exhausted, every
// further call hangs until aborted, so the poll loop parks instead of spinning once the
// scripted sequence ends.
function sequencedPollMock(responses) {
  let i = 0
  return vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
    if (i < responses.length) {
      const entry = responses[i]
      i++
      if (typeof entry === 'function') return entry(_url, opts)
      return Promise.resolve({ ok: true, json: () => Promise.resolve(entry) })
    }
    return hangingAbortAwareFetch(_url, opts)
  })
}

// Duplicated from equivalence.test.js's own helper of the same name: production always
// runs hydrateBackgroundAgents() (or, for the live path, applies each task-lifecycle
// frame incrementally via applyRecord()'s own 'live'-gated call — see message.js)
// before loadMessages()'s own narration-routing pass depends on
// taskIdByLaunchToolUseId already being populated. This harness has no background_agents
// snapshot endpoint to mock, so it reconstructs the same state by replaying each stored
// Task lifecycle frame through the exact same per-frame reducer the live path uses.
function applyStoredTaskLifecycleFrames(messageStore, sessionId, rawMessages) {
  for (const msg of rawMessages) {
    if (msg.type === 'system') {
      messageStore.applyTaskLifecycleFrame(sessionId, msg.metadata?.subtype, msg.metadata, msg.timestamp)
    }
  }
}

// Trimmed, local duplicate of equivalence.test.js's replayRestPath() — only the two
// fields this suite compares (messagesBySession/toolCallsBySession), in a fresh Pinia
// instance. This is every scenario's "cold, independent reload" ground truth.
async function coldRestReload(fixture) {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const messageStore = useMessageStore()

  apiMock.get.mockImplementation((url) => {
    if (String(url).includes(`/api/sessions/${fixture.sessionId}/messages`)) {
      return Promise.resolve(fixture.restHistory)
    }
    return Promise.resolve({})
  })

  applyStoredTaskLifecycleFrames(messageStore, fixture.sessionId, fixture.restHistory.messages ?? [])
  await messageStore.loadMessages(fixture.sessionId)

  return {
    messages: messageStore.messagesBySession.get(fixture.sessionId) || [],
    toolCalls: messageStore.toolCallsBySession.get(fixture.sessionId) || [],
  }
}

function snapshotMessageState(messageStore, sessionId) {
  return {
    messages: messageStore.messagesBySession.get(sessionId) || [],
    toolCalls: messageStore.toolCallsBySession.get(sessionId) || [],
  }
}

// AC1-style fail-not-skip guard (mirrored from equivalence.test.js): listRawFixtureNames()
// already throws (rather than returning an empty list) when no fixture is discoverable,
// so letting it propagate here fails this suite loudly instead of silently running zero
// tests.
const fixtureNames = listRawFixtureNames()
const fixture = loadRawFixture(fixtureNames[0])

describe(`reset/evicted/backend-restart/unreachable path equivalence (issue #2160 AC3) — fixture "${fixture.name}"`, () => {
  it('session-stream stream_reset mid-poll triggers a loadMessages() resync that converges with a cold REST reload', async () => {
    setActivePinia(createPinia())
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const { usePollingStore } = await import('@/stores/polling')

    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()
    const pollingStore = usePollingStore()

    sessionStore.sessions.set(fixture.sessionId, makeSession({ session_id: fixture.sessionId }))
    sessionStore.currentSessionId = fixture.sessionId

    apiMock.get.mockImplementation((url) => {
      if (String(url).includes('/cursor')) return Promise.resolve({ cursor: 0 })
      if (String(url).includes(`/api/sessions/${fixture.sessionId}/messages`)) {
        return Promise.resolve(fixture.restHistory)
      }
      return Promise.resolve({})
    })

    // Every real recorded event is replayed live FIRST (so messagesBySession/
    // toolCallsBySession build up the normal way, and — critically — so
    // taskLegsByTaskId/taskIdByLaunchToolUseId gets populated the same way
    // applyStoredTaskLifecycleFrames() populates it for the cold reload below, via
    // applyRecord()'s own 'live'-gated task-lifecycle-frame application), THEN a
    // stream_reset marker arrives in the same batch, forcing the resync's full
    // loadMessages() REST reload to overwrite messagesBySession/toolCallsBySession
    // wholesale with the fixture's rest_history.
    const resetEvent = { type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }
    sequencedPollMock([
      {
        ui: { events: [], next_cursor: 1 },
        sessions: {
          [fixture.sessionId]: {
            events: [...fixture.queueEvents, resetEvent],
            next_cursor: fixture.queueEvents.length + 1,
          },
        },
      },
    ])

    await pollingStore.connectSession(fixture.sessionId)
    await flush()
    await flush()
    await flush()

    expect(pollingStore.sessionState).toBe('connected')

    const live = snapshotMessageState(messageStore, fixture.sessionId)
    expect(live.messages.length).toBeGreaterThan(0)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()

    const cold = await coldRestReload(fixture)

    expect(normalizeForComparison(live.messages)).toEqual(normalizeForComparison(cold.messages))
    expect(normalizeForComparison(live.toolCalls)).toEqual(normalizeForComparison(cold.toolCalls))
  })

  it('session-stream evicted:true (no events) triggers the same loadMessages() resync path, converging with a cold REST reload', async () => {
    setActivePinia(createPinia())
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const { usePollingStore } = await import('@/stores/polling')

    const sessionStore = useSessionStore()
    const messageStore = useMessageStore()
    const pollingStore = usePollingStore()

    sessionStore.sessions.set(fixture.sessionId, makeSession({ session_id: fixture.sessionId }))
    sessionStore.currentSessionId = fixture.sessionId

    apiMock.get.mockImplementation((url) => {
      if (String(url).includes('/cursor')) return Promise.resolve({ cursor: 0 })
      if (String(url).includes(`/api/sessions/${fixture.sessionId}/messages`)) {
        return Promise.resolve(fixture.restHistory)
      }
      return Promise.resolve({})
    })

    // Cycle 1: the full recorded event stream is applied live (same task-lifecycle
    // pre-seeding reasoning as the stream_reset scenario above) while the session is
    // genuinely connected and current — exactly the realistic precondition for an
    // eviction (an already-subscribed session whose cursor falls outside the server's
    // retained ring-buffer window), not a cold-first-connect eviction. Cycle 2: the
    // eviction itself, carrying NO events at all (the real evicted contract — an
    // evicted cursor can't be trusted to deliver a gapless slice, so the batch is never
    // even populated with one).
    sequencedPollMock([
      {
        ui: { events: [], next_cursor: 1 },
        sessions: { [fixture.sessionId]: { events: fixture.queueEvents, next_cursor: fixture.queueEvents.length } },
      },
      {
        ui: { events: [], next_cursor: 2 },
        sessions: { [fixture.sessionId]: { evicted: true, next_cursor: fixture.queueEvents.length + 1 } },
      },
    ])

    await pollingStore.connectSession(fixture.sessionId)
    await flush()
    await flush()
    await flush()
    await flush()

    expect(pollingStore.sessionState).toBe('connected')

    const live = snapshotMessageState(messageStore, fixture.sessionId)
    expect(live.messages.length).toBeGreaterThan(0)

    await pollingStore.disconnectSession()
    pollingStore.stopUIPolling()

    const cold = await coldRestReload(fixture)

    expect(normalizeForComparison(live.messages)).toEqual(normalizeForComparison(cold.messages))
    expect(normalizeForComparison(live.toolCalls)).toEqual(normalizeForComparison(cold.toolCalls))
  })

  it('UI-stream stream_reset ("a Backend restart made explicit") triggers loadAppData(), converging with a direct cold fetchProjects()+fetchSessions() call', async () => {
    setActivePinia(createPinia())
    const { useSessionStore } = await import('@/stores/session')
    const { useProjectStore } = await import('@/stores/project')
    const { usePollingStore } = await import('@/stores/polling')

    const sessionStore = useSessionStore()
    const projectStore = useProjectStore()
    const pollingStore = usePollingStore()

    // No naturally-given project/session LIST payload exists in a raw_log/rest_history
    // fixture (those capture one session's own message stream, not the app-wide
    // /api/projects or /api/sessions responses) — these are hand-built, but still tied
    // to the fixture via its real sessionId, and used identically on both the live and
    // cold sides below (the "identical mocked REST responses" the task calls for).
    const sessionsPayload = { sessions: [makeSession({ session_id: fixture.sessionId, name: 'Reset-path session' })] }
    const projectsPayload = { projects: [makeProject({ project_id: 'proj-reset-path', session_ids: [fixture.sessionId] })] }

    apiMock.get.mockImplementation((url) => {
      if (url === '/api/projects') return Promise.resolve(projectsPayload)
      if (url === '/api/sessions') return Promise.resolve(sessionsPayload)
      return Promise.resolve({})
    })

    sequencedPollMock([
      {
        ui: { events: [{ type: 'stream_reset', data: { old_epoch: 'e1', new_epoch: 'e2' } }], next_cursor: 5 },
        sessions: {},
      },
    ])

    pollingStore.startUIPolling()
    await flush()
    await flush()
    await flush()

    expect(pollingStore.uiState).toBe('connected')

    const liveSessions = new Map(sessionStore.sessions)
    const liveProjects = new Map(projectStore.projects)

    pollingStore.stopUIPolling()

    // Cold, independent reload: same apiMock responses, driven directly (no polling
    // loop, no fault), in a fresh Pinia instance.
    setActivePinia(createPinia())
    const sessionStoreCold = useSessionStore()
    const projectStoreCold = useProjectStore()
    await Promise.all([projectStoreCold.fetchProjects(), sessionStoreCold.fetchSessions()])

    expect(liveSessions.size).toBeGreaterThan(0)
    expect(normalizeForComparison([...liveSessions.values()]))
      .toEqual(normalizeForComparison([...sessionStoreCold.sessions.values()]))
    expect(normalizeForComparison([...liveProjects.values()]))
      .toEqual(normalizeForComparison([...projectStoreCold.projects.values()]))
  })

  it('fetch() rejecting then recovering (backoff -> connecting) delivers the full live event stream intact, converging with a cold REST reload', async () => {
    vi.useFakeTimers()
    try {
      setActivePinia(createPinia())
      const { useSessionStore } = await import('@/stores/session')
      const { useMessageStore } = await import('@/stores/message')
      const { usePollingStore } = await import('@/stores/polling')

      const sessionStore = useSessionStore()
      const messageStore = useMessageStore()
      const pollingStore = usePollingStore()

      sessionStore.sessions.set(fixture.sessionId, makeSession({ session_id: fixture.sessionId }))
      sessionStore.currentSessionId = fixture.sessionId

      apiMock.get.mockImplementation((url) => {
        if (String(url).includes('/cursor')) return Promise.resolve({ cursor: 0 })
        return Promise.resolve({})
      })

      let callCount = 0
      vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
        callCount++
        if (callCount === 1) {
          return Promise.reject(new Error('Connection refused'))
        }
        if (callCount === 2) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
              ui: { events: [], next_cursor: 1 },
              sessions: {
                [fixture.sessionId]: { events: fixture.queueEvents, next_cursor: fixture.queueEvents.length },
              },
            }),
          })
        }
        return hangingAbortAwareFetch(_url, opts)
      })

      await pollingStore.connectSession(fixture.sessionId)
      await vi.advanceTimersByTimeAsync(0)
      expect(pollingStore.sessionState).toBe('backoff')
      expect(pollingStore.sessionRetryCount).toBe(1)

      // 2000ms: this fixture's single retry-count bump means delay = 2000 * 1 (see
      // polling.js's `Math.min(2000 * retryCount, 30000)`) — same amount
      // polling.test.js's own "backoff + timer fires -> connecting" test uses.
      await vi.advanceTimersByTimeAsync(2000)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(0)

      // >= 2, not strictly 2: once the successful 2nd call lands and the state
      // returns to 'connected', the loop immediately re-enters with another poll
      // (parked via hangingAbortAwareFetch) — a 3rd call is a harmless, timing-
      // dependent side effect of that immediate re-entry, not a correctness signal.
      expect(callCount).toBeGreaterThanOrEqual(2)
      expect(pollingStore.sessionState).toBe('connected')
      expect(pollingStore.sessionRetryCount).toBe(0)

      const live = snapshotMessageState(messageStore, fixture.sessionId)
      expect(live.messages.length).toBeGreaterThan(0)

      await pollingStore.disconnectSession()
      pollingStore.stopUIPolling()

      const cold = await coldRestReload(fixture)

      expect(normalizeForComparison(live.messages)).toEqual(normalizeForComparison(cold.messages))
      expect(normalizeForComparison(live.toolCalls)).toEqual(normalizeForComparison(cold.toolCalls))
    } finally {
      vi.useRealTimers()
    }
  })
})
