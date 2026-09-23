import { describe, it, expect, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import {
  listRawFixtureNames,
  loadRawFixture,
  normalizeForComparison,
} from './helpers/fixtureEquivalence'

// Issue #1999 (US1, AC1/AC2): feeds each recorded raw fixture (backend/tests/fixtures/raw/)
// through the live event path and the REST reload path, and fails if messagesBySession/
// toolCallsBySession diverge — catches a message-pipeline regression (e.g. #1962, #1951)
// before merge. Also covers T1 (issue #1998 follow-up): every raw fixture under
// backend/tests/fixtures/raw/ — not just the one real, owner-only-regenerable primary
// capture — is discovered dynamically and run through this same check.
//
// Deliberately does NOT route through mock_sdk.py's raw-layer replay: it replays
// raw_log.jsonl's queue_event stream directly into polling.js's real poll loop (so
// handleSessionMessage() — an internal, unexported closure — still runs via its only
// real entry point, the session poll loop), and separately drives message.js's
// loadMessages() against rest_history.json. See PLAN_1999's Risks section for why this
// diverges from a hint left in #1998's mock_sdk.py docstrings.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

function flush() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

// Serves `batch` (a single {events, next_cursor} poll response) to the first fetch()
// call, then hangs (abort-aware, like the real long-poll's idle wait) for every call
// after — so the poll loop's first response carries the entire recorded queue_event
// stream, and every call after that parks harmlessly until disconnectSession() aborts it.
function singleBatchFetchMock(batch) {
  let served = false
  return vi.spyOn(global, 'fetch').mockImplementation((_url, opts) => {
    if (!served) {
      served = true
      return Promise.resolve({ ok: true, json: () => Promise.resolve(batch) })
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

async function replayLivePath(fixture) {
  setActivePinia(createPinia())
  const { useSessionStore } = await import('@/stores/session')
  const { useMessageStore } = await import('@/stores/message')
  const { usePollingStore } = await import('@/stores/polling')

  const sessionStore = useSessionStore()
  const messageStore = useMessageStore()
  const pollingStore = usePollingStore()

  // handleSessionMessage() only applies an event when session.js's own currentSessionId
  // matches — mirrors how the real app keeps them in sync via selectSession().
  sessionStore.currentSessionId = fixture.sessionId

  apiMock.get.mockImplementation((url) => {
    if (String(url).includes('/cursor')) return Promise.resolve({ cursor: 0 })
    return Promise.resolve({})
  })
  const fetchMock = singleBatchFetchMock({
    events: fixture.queueEvents,
    next_cursor: fixture.queueEvents.length,
  })

  await pollingStore.connectSession(fixture.sessionId)
  await flush()
  await flush()
  await pollingStore.disconnectSession()
  fetchMock.mockRestore()

  return {
    messages: messageStore.messagesBySession.get(fixture.sessionId) || [],
    toolCalls: messageStore.toolCallsBySession.get(fixture.sessionId) || [],
  }
}

async function replayRestPath(fixture) {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const messageStore = useMessageStore()

  apiMock.get.mockImplementation((url) => {
    if (String(url).includes(`/api/sessions/${fixture.sessionId}/messages`)) {
      return Promise.resolve(fixture.restHistory)
    }
    return Promise.resolve({})
  })

  await messageStore.loadMessages(fixture.sessionId)

  return {
    messages: messageStore.messagesBySession.get(fixture.sessionId) || [],
    toolCalls: messageStore.toolCallsBySession.get(fixture.sessionId) || [],
  }
}

// Issue #1999 (AC1/AC2) found a genuine, pre-existing divergence between the live
// event path and the REST reload path that predates this issue entirely — NOT a
// message-pipeline regression this harness is meant to catch, and out of scope to fix
// here (this issue is test-infrastructure-only). Tracked as issue #2002: backend/
// session_coordinator.py's _convert_stored_message_to_websocket() reconstructs
// messages from stored StoredMessage rows for a REST reload without running them
// through DisplayProjection or message_parser.py's default-content/default-metadata
// synthesis the live path always applies — e.g. every "init"-subtype system message
// reloads with content:"" instead of the live path's synthesized "System message", and
// dozens of has_*/tool_uses/tool_results/thinking/display/model/usage metadata keys are
// simply absent rather than explicitly false/empty. Measured against the primary
// fixture: ~76% of non-tool_call messages differ in `content` on reload, and nearly
// every message is missing 5-15 metadata keys the live path always sets.
//
// mock-sdk-synthetic (T1's builder-regenerable fixture) independently reproduces the
// same divergence: its rest_history.json is built by reprocessing the real stored
// messages.jsonl through the SAME real _convert_stored_message_to_websocket() method
// (see generate_synthetic_fixture.py's _reconstruct_rest_history_messages() —
// deliberately NOT built from the live path's own accumulator, which would make this
// check tautological and prove nothing about the harness's ability to catch a genuine
// divergence). Confirming the same bug shows up on synthetic data too is useful
// evidence #2002 is a systemic backend gap, not an artifact of one real recording — not
// a reason to weaken this harness.
//
// A normalizer permissive enough to hide this would also hide a real regression,
// defeating AC2's purpose — so both fixtures are captured as it.fails() expected
// failures, naming the fixture and citing #2002, rather than silently skipped or
// normalized away. it.fails() means: if #2002 is fixed and either of these starts
// passing, the suite FAILS until someone removes that fixture's entry here — the test
// can't silently go stale, and this file is the single place to update when that happens.
const KNOWN_DIVERGENT_FIXTURES = new Map([
  ['2026-09-23-primary', 'issue #2002 — see comment above'],
  ['mock-sdk-synthetic', 'issue #2002 — see comment above'],
])

describe('fixture equivalence — live event path vs. REST reload path (issue #1999, AC1/AC2)', () => {
  // AC1's fail-not-skip guard: listRawFixtureNames() throws (rather than returning an
  // empty list) if backend/tests/fixtures/raw/ is empty or missing, so a misconfigured
  // checkout fails this suite loudly instead of silently reporting zero tests.
  const fixtureNames = listRawFixtureNames()
  const expectedToConverge = fixtureNames.filter(name => !KNOWN_DIVERGENT_FIXTURES.has(name))
  const expectedToDiverge = fixtureNames.filter(name => KNOWN_DIVERGENT_FIXTURES.has(name))

  async function checkConvergence(name) {
    const fixture = loadRawFixture(name)

    const live = await replayLivePath(fixture)
    const rest = await replayRestPath(fixture)

    expect(live.messages.length).toBeGreaterThan(0)
    expect(normalizeForComparison(live.messages)).toEqual(normalizeForComparison(rest.messages))
    expect(normalizeForComparison(live.toolCalls)).toEqual(normalizeForComparison(rest.toolCalls))
  }

  it.each(expectedToConverge)('fixture "%s": messagesBySession and toolCallsBySession converge', checkConvergence)

  // Deliberately not `it.each(...).skip` — an expected-failure still runs the full
  // comparison every time and fails the suite the moment it unexpectedly starts
  // passing, so a backend fix can't silently go unnoticed here.
  it.fails.each(expectedToDiverge)(
    'fixture "%s": KNOWN pre-existing divergence (see KNOWN_DIVERGENT_FIXTURES) — expected to fail until fixed in backend/',
    checkConvergence
  )
})
