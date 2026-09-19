import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import HeaderRow1 from '@/components/layout/HeaderRow1.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn().mockResolvedValue({}), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('HeaderRow1', () => {
  it('renders the connection indicator without visible text, using aria-label instead', async () => {
    const { pinia } = renderWithStores(HeaderRow1)

    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore(pinia)

    pollingStore.uiConnected = true
    await new Promise(r => setTimeout(r, 0))

    const indicator = screen.getByTestId('connection-indicator')
    expect(indicator.textContent.trim()).toBe('')
    expect(indicator.getAttribute('role')).toBe('status')
    expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    expect(indicator.classList.contains('connected')).toBe(true)

    pollingStore.uiConnected = false
    await new Promise(r => setTimeout(r, 0))

    expect(indicator.textContent.trim()).toBe('')
    expect(indicator.getAttribute('aria-label')).toBe('Connection status: Disconnected')
    expect(indicator.classList.contains('disconnected')).toBe(true)
  })

  it('right-clicking the connection indicator submits the debug buffer and shows a confirmation (issue #1931)', async () => {
    renderWithStores(HeaderRow1)

    const indicator = screen.getByTestId('connection-indicator')
    await fireEvent.contextMenu(indicator)

    expect(await screen.findByText('✓ Sent')).toBeTruthy()
    expect(apiMock.post).toHaveBeenCalledWith('/api/debug/client-buffer', expect.objectContaining({
      reason: 'manual'
    }))
  })

  // Issue #1960: the single header dot must also reflect the session-poll channel
  // (previously it only ever looked at uiConnected, so a silently-stalled session
  // connection was invisible even though the store already detected it).
  describe('session-poll channel merge (#1960)', () => {
    async function setup() {
      const { pinia } = renderWithStores(HeaderRow1)
      const { usePollingStore } = await import('@/stores/polling')
      const { useSessionStore } = await import('@/stores/session')
      const pollingStore = usePollingStore(pinia)
      const sessionStore = useSessionStore(pinia)
      pollingStore.uiConnected = true
      pollingStore.uiRetryCount = 0
      await new Promise(r => setTimeout(r, 0))
      return { pollingStore, sessionStore, indicator: screen.getByTestId('connection-indicator') }
    }

    it('ignores session state entirely when no session poll loop is running (regression guard)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = null
      pollingStore.sessionConnected = false
      pollingStore.sessionRetryCount = 0
      pollingStore.sessionStalled = true
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('connected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    })

    // Issue #1960 (found in review): archive/deleted-agent views set
    // sessionStore.currentSessionId directly (SessionView.vue's onActivated, archive
    // branch) WITHOUT ever calling wsStore.connectSession() — there is no real poll
    // loop for an archive. Gating on sessionStore.currentSessionId previously showed a
    // false "disconnected" dot for the entire time an archive was open even though the
    // UI-poll channel was perfectly healthy. The gate must be wsStore's own
    // currentSessionId (which archive mode never touches), not sessionStore's.
    it('shows connected (not disconnected) while viewing an archive that set sessionStore.currentSessionId without starting a poll loop', async () => {
      const { pollingStore, sessionStore, indicator } = await setup()
      sessionStore.currentSessionId = 'archived-sess-1'
      pollingStore.currentSessionId = null
      pollingStore.sessionConnected = false
      pollingStore.sessionRetryCount = 0
      pollingStore.sessionStalled = false
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('connected')).toBe(true)
      expect(indicator.classList.contains('disconnected')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    })

    it('shows stalled when a session poll loop is running, connected, and its heartbeat is dead', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionConnected = true
      pollingStore.sessionRetryCount = 0
      pollingStore.sessionStalled = true
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('stalled')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Stalled')
    })

    it('shows reconnecting (not stalled) when the session channel is retrying — precedence check', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionConnected = false
      pollingStore.sessionRetryCount = 2
      pollingStore.sessionStalled = true
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('reconnecting')).toBe(true)
      expect(indicator.classList.contains('stalled')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Reconnecting')
    })

    it('a healthy session channel does not override a broken UI channel — UI disconnect still wins', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionConnected = true
      pollingStore.sessionRetryCount = 0
      pollingStore.sessionStalled = false
      pollingStore.uiConnected = false
      pollingStore.uiRetryCount = 3
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('reconnecting')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Reconnecting')
    })

    it('a stalled session channel does not override an outright-disconnected UI channel — UI disconnect still wins', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionConnected = true
      pollingStore.sessionRetryCount = 0
      pollingStore.sessionStalled = true
      pollingStore.uiConnected = false
      pollingStore.uiRetryCount = 0
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('disconnected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Disconnected')
    })

    it('shows connected when both channels are healthy (regression guard)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionConnected = true
      pollingStore.sessionRetryCount = 0
      pollingStore.sessionStalled = false
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('connected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    })
  })
})
