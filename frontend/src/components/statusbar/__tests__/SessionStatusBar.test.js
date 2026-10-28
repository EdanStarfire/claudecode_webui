import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import { makeSession } from '@/test-utils/factories'
import { createFakeHistoryServer } from '@/stores/__tests__/helpers/historyServer'
import SessionStatusBar from '@/components/statusbar/SessionStatusBar.vue'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-1'

// Issue #2030 (stage 1b): turn navigation indexes the LOADED list, so it is gated until the whole
// history has arrived (stage 3 adds a server turn index).
describe('SessionStatusBar turn navigation gating (issue #2030)', () => {
  beforeEach(() => apiMock.get.mockReset())

  async function mountBar() {
    const virtualNav = { findPrevTurnIndex: () => 1, findNextTurnIndex: () => 5, scrollToItemIndex: vi.fn() }
    const rendered = renderWithStores(SessionStatusBar, { props: { sessionId: SID, virtualNav } })
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    useSessionStore(rendered.pinia).sessions.set(SID, makeSession({ session_id: SID }))
    return { ...rendered, messageStore: useMessageStore(rendered.pinia) }
  }

  const prev = () => document.querySelector('[aria-label="Jump to previous turn"]')
  const next = () => document.querySelector('[aria-label="Jump to next turn"]')

  it('enables both buttons for a session that was loaded in full', async () => {
    await mountBar()
    expect(prev().disabled).toBe(false)
    expect(next().disabled).toBe(false)
  })

  it('disables both with a "Loading older history…" tooltip until the backfill completes, then enables them', async () => {
    const { messageStore } = await mountBar()
    const records = Array.from({ length: 6 }, (_, i) => ({ type: 'user', message_id: `m${i}`, timestamp: i, content: 'x', metadata: {} }))
    const server = createFakeHistoryServer(records, { tailStart: 3, pageSize: 10 })
    apiMock.get.mockImplementation((url) => (String(url).includes('/messages') ? server.handle(url) : Promise.resolve({})))
    await messageStore.loadMessages(SID, null, 0, { window: 'tail', signal: new AbortController().signal })
    await new Promise(r => setTimeout(r, 10))

    expect(prev().disabled).toBe(true)
    expect(next().disabled).toBe(true)
    expect(prev().title).toBe('Loading older history…')

    messageStore.applyFetchedOlderPage(SID, await messageStore.fetchOlderPage(SID, { signal: new AbortController().signal }))
    await new Promise(r => setTimeout(r, 10))
    expect(prev().disabled).toBe(false)
    expect(next().disabled).toBe(false)
    expect(prev().title).toBe('Jump to previous user message or comm')
  })
})
