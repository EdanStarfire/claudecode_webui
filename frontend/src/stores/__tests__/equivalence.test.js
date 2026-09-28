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
// always applies — e.g. every "init"-subtype system message reloaded with content:""
// instead of the live path's synthesized "System message", and dozens of
// has_*/tool_uses/tool_results/thinking/display/model/usage metadata keys were simply
// absent rather than explicitly false/empty. Measured against the primary fixture:
// ~76% of non-tool_call messages differed in `content` on reload, and nearly every
// message was missing 5-15 metadata keys the live path always sets.
//
// Fixed in issue #2006 (field-parity in _convert_stored_message_to_websocket() plus a
// request-local DisplayProjection replay — with pagination-prefix replay so tools that
// completed on an earlier page still showed correctly on a later one — in
// get_session_messages()/get_archive_messages()). #2006 shipped a production
// regression: the DisplayProjection prefix replay ran the entire discarded [0, offset)
// prefix synchronously on the event loop on every paged request, which is quadratic in
// session length — confirmed in production by issue #2026, where a 22,334-record
// session froze the Backend for 1-3+ minutes, disconnecting every client. #2006's
// reload-path additions were reverted in issue #2028 (keeping #2014's independent
// live-path fixes — stderr storage, interrupt_success removal, DisplayProjection
// content-block feed — which never depended on the reverted code), restoring #main to
// a safe-to-run-in-production state while the correct non-quadratic, non-blocking fix
// that preserves #2006's parity goal is worked on in #2026. This reopens #2002: both
// fixtures below are expected to diverge again for exactly #2002's original reason
// until #2026 lands.
//
// mock-sdk-synthetic (T1's builder-regenerable fixture) independently reproduced the
// same divergence prior to #2026 (its rest_history.json is built by reprocessing the
// real stored messages.jsonl through the SAME real _convert_stored_message_to_
// websocket() method — see generate_synthetic_fixture.py's
// _reconstruct_rest_history_messages() — deliberately NOT built from the live path's
// own accumulator, which would make this check tautological). #2026 closed the gap
// this method causes (Part A's content/metadata synthesis, plus persisting the
// live-computed `display` value instead of never storing it at all) without
// reintroducing #2006's quadratic replay-at-read-time — see #2026's Risk Analysis for
// the structural argument for why replay-at-read-time was the wrong shape regardless
// of caching. This fixture now converges and is removed from the map below; confirming
// that on regenerable synthetic data (not just by inspection) is what makes this
// removal trustworthy rather than asserted.
//
// 2026-09-23-primary (issue #2017) was re-captured live against a real Anthropic API
// (claude-agent-sdk==0.2.159), on a checkout that already includes #2032's reload/live
// display-parity fix — so the OLD reason this fixture used to diverge (a frozen
// pre-#2007 capture containing a captured interrupt_success event, un-stored stderr
// lines, and display.tool_states == {} from the old no-op projection) no longer
// applies: this file has none of that. Verified: grepping the re-captured
// raw_log.jsonl/rest_history.json for "interrupt_success" and "stderr" finds zero
// matches, and display.tool_states now carries real per-tool entries on both the live
// and reload paths (e.g. {"toolu_...": {"state": "completed", ...}}), not `{}`.
//
// The re-capture still diverges, but for different, currently-real gaps in
// backend/session_coordinator.py's _convert_stored_message_to_websocket() that #2032's
// port of message_parser.py's defaults left incomplete:
//   1. SystemMessage/HookEventMessage reload metadata never sets the six keys
//      message_parser.py's SystemMessageHandler always defaults live (`working_directory`,
//      `permissions`, `tools`, `model`, `system_prompt`, `error_details`) — only the five
//      has_*/session_id keys are ported (session_coordinator.py's shared setdefault
//      block). Affects ~210 of this fixture's 365 messages (every system message).
//   2. `display` is entirely absent on reload for `client_launched` and `interrupt`
//      subtypes — #2032's pre-store display_hook apparently isn't wired for whatever
//      code path stores these two subtypes.
//   3. TaskUpdatedMessage reload sets `tool_use_id` (always null) but never
//      `task_session_id`, which the live path sets and the reload path omits — a
//      field-name mismatch, not a missing-default gap.
//   4. Tool-result content that was a plain string live is wrapped as
//      `[{"type": "text", "text": ...}]` on reload — a storage/reconstruction
//      normalization difference for UserMessage tool_result blocks.
//   5. Two user messages (a Legion comm report, an attachment message) reload with
//      *only* their `comm`/`attachments` metadata key and none of the usual has_*/
//      session_id/tool_uses/tool_results/role defaults — they appear to hit a legacy
//      dict-shaped conversion branch that skips the shared defaults block entirely.
// None of these were introduced by #2017's re-capture — they're pre-existing gaps in
// the reload path that this fixture (now free of its old, unrelated divergence) is the
// first to surface. Not fixed here: out of scope for a fixture re-capture, and each is
// backend/session_coordinator.py work, not scenario_driver work.
//
// A normalizer permissive enough to hide this would also hide a real regression,
// defeating AC2's purpose — so this fixture stays captured as an it.fails() expected
// failure, naming the fixture and citing the relevant issue, rather than silently
// skipped or normalized away. it.fails() means: if it starts passing, the suite FAILS
// until someone removes its entry here — the test can't silently go stale, and this
// file is the single place to update when that happens.
const KNOWN_DIVERGENT_FIXTURES = new Map([
  [
    '2026-09-23-primary',
    'reload-path gaps in _convert_stored_message_to_websocket() left after #2032 ' +
    '(missing SystemMessage metadata defaults, missing display for client_launched/' +
    'interrupt, TaskUpdatedMessage task_session_id, tool_result content-block ' +
    'normalization, legacy-branch comm/attachment messages) — see comment above',
  ],
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
