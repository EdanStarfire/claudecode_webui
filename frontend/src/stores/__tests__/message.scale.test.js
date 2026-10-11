import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { loadScaledRestHistory } from './helpers/scaleFixture'

// Issue #2131 (AC3): the cold-load regression this issue exists to fix only manifests at
// real-world scale (the reported freeze was at N=92,480) — a small fixture can't exercise it.
// Scales the committed '2026-09-23-primary' rest_history.json to 100,000+ records via
// scaleFixture.js (see its own comment for why this isn't a reuse of
// backend/tests/fixtures/scale_fixture.py) and drives it through the real loadMessages() path.

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

const SID = 'scale-sess-2131'
const TARGET_COUNT = 100000

describe('cold load scale regression (Issue #2131, AC3)', () => {
  it('loads 100,000+ records under budget with correct identity dedup, including a redelivered subset', async () => {
    const scaled = loadScaledRestHistory('2026-09-23-primary', TARGET_COUNT)
    expect(scaled.length).toBeGreaterThanOrEqual(TARGET_COUNT)

    // (c): simulate a redelivered tail subset (e.g. a reconnect replaying the most recent
    // page) — these carry IDENTICAL identity fields to records already in `scaled`, so a
    // correct dedup must skip every one of them rather than double-counting.
    const redelivered = scaled.slice(-500)
    const withRedelivery = [...scaled, ...redelivered]

    apiMock.get.mockResolvedValueOnce({
      messages: withRedelivery,
      total_count: withRedelivery.length,
      has_more: false,
      event_cursor: 999,
    })

    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    const start = performance.now()
    // Explicit limit: today's single-shot-fetch branch (one api.get call), avoiding
    // fetchAllMessagePages' pagination loop — irrelevant to the dedup/index fix under test.
    await store.loadMessages(SID, withRedelivery.length)
    const elapsedMs = performance.now() - start

    // (a): budget benchmarked with generous headroom — see the PR description for the
    // pre-fix (O(n^2) `.some`/`.findIndex` scan) vs post-fix (O(1) index Map) numbers this
    // was picked from. The pre-fix shape alone (ignoring the reactive-proxy overhead the
    // issue also measured) is already ~10s of comparisons at this scale; this fix lands in
    // well under a second of real store work.
    expect(elapsedMs).toBeLessThan(8000)

    // (b): exactly the unique set of non-tool_call records lands in messagesBySession —
    // the redelivered tail must not inflate the count. Excludes the pre-existing (#1486/
    // #1575), unrelated-to-this-fix suppression of internal system/status/requesting
    // messages, which applyRecord drops before identity dedup ever sees them.
    const isSuppressedStatusMessage = m =>
      m.type === 'system' &&
      (m.subtype || m.metadata?.subtype) === 'status' &&
      m.metadata?.init_data?.status === 'requesting'
    const uniqueMessageIds = new Set(
      scaled
        .filter(m => m.type !== 'tool_call' && !isSuppressedStatusMessage(m))
        .map(m => m.message_id)
    )
    const storedMessages = store.messagesBySession.get(SID)
    expect(storedMessages.length).toBe(uniqueMessageIds.size)

    const uniqueToolUseIds = new Set(
      scaled.filter(m => m.type === 'tool_call').map(m => m.tool_use_id)
    )
    const storedToolCalls = store.toolCallsBySession.get(SID)
    expect(storedToolCalls.length).toBe(uniqueToolUseIds.size)

    // Index maps stay 1:1 with the arrays they mirror.
    expect(store.messageIndexBySession.get(SID)?.size).toBe(uniqueMessageIds.size)
    expect(store.toolIndexBySession.get(SID)?.size).toBe(uniqueToolUseIds.size)
  }, 30000)
})
