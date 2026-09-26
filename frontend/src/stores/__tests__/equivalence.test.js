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
// event path and the REST reload path: backend/session_coordinator.py's
// _convert_stored_message_to_websocket() reconstructed messages from stored
// StoredMessage rows for a REST reload without running them through DisplayProjection
// or message_parser.py's default-content/default-metadata synthesis the live path
// always applies. Fixed in issue #2002 (field-parity in
// _convert_stored_message_to_websocket() plus a request-local DisplayProjection replay
// — with pagination-prefix replay so tools that completed on an earlier page still
// show correctly on a later one — in get_session_messages()/get_archive_messages()).
//
// mock-sdk-synthetic (independently rebuilt by reprocessing messages.jsonl through the
// same real _convert_stored_message_to_websocket()) now converges fully and runs
// through the normal it.each() below.
//
// 2026-09-23-primary still diverges, but no longer for #2002's reason — every field
// #2002 measured (content synthesis, ~40 metadata keys, tool_results content-block
// joining, local-command-response unwrapping, etc.) now matches exactly. What's left
// is two narrower, genuinely separate, pre-existing gaps in the LIVE path itself
// (not the REST reload path #2002 touches), found by diffing this real fixture's raw
// captured live events against messages.jsonl:
//   1. Three live-only system messages (interrupt_success; two hook "stderr" events)
//      are injected directly into _create_message_callback(), bypassing
//      ClaudeSDK._store_sdk_message() entirely — they are never written to
//      messages.jsonl at all, so no reload-time reconstruction can recover them.
//   2. DisplayProjection is a structural no-op on the live path for ordinary
//      messages: _create_message_callback() feeds it a StoredMessage built from
//      legacy_to_stored({content: parsed_message.content, ...parsed_message.metadata}),
//      and StoredMessage.get_tool_uses()/get_tool_results() only ever look at
//      data["content"] — which here is parsed_message.content, always a
//      human-readable string, never the real content-block list. Confirmed against
//      this fixture's raw_log.jsonl: 515 captured live messages carry a `display` key,
//      and `tool_states` is `{}` on every single one. Reload's DisplayProjection
//      replay (Gap 2) reads the real StoredMessage content-block list instead, so it
//      correctly tracks tool lifecycle — matching Gap 2b's own required regression
//      test — which makes it diverge from what live actually (if wrongly) ships today.
// Both are live-write/live-callback-pipeline bugs, explicitly out of #2002's scope
// (see its plan's "Not in scope" section) and orthogonal to each other and to #2002's
// original finding. Filed as issue #2007, with a severity caveat: message.js drives
// live tool-card status from dedicated ToolCallUpdate payloads (a separate,
// actively-maintained path — see get_session_messages()'s "they already carry their
// own baked-in display state" comment), so gap 2 above likely doesn't break the
// primary tool-card UI even though DisplayProjection itself is a no-op on live.
const KNOWN_DIVERGENT_FIXTURES = new Map([
  ['2026-09-23-primary', 'issue #2007 — see comment above, not #2002'],
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
