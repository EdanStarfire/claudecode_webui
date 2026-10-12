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

    pollingStore.uiState = 'connected'
    await new Promise(r => setTimeout(r, 0))

    const indicator = screen.getByTestId('connection-indicator')
    expect(indicator.textContent.trim()).toBe('')
    expect(indicator.getAttribute('role')).toBe('status')
    expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    expect(indicator.classList.contains('connected')).toBe(true)

    pollingStore.uiState = 'idle'
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

  // Epic #1990 stage 5b: the single header dot derives directly from each
  // StreamConnection's own `state` — no more wall-clock stall inference.
  describe('session-poll channel merge (#1960, stage 5b)', () => {
    async function setup() {
      const { pinia } = renderWithStores(HeaderRow1)
      const { usePollingStore } = await import('@/stores/polling')
      const { useSessionStore } = await import('@/stores/session')
      const pollingStore = usePollingStore(pinia)
      const sessionStore = useSessionStore(pinia)
      pollingStore.uiState = 'connected'
      pollingStore.uiRetryCount = 0
      await new Promise(r => setTimeout(r, 0))
      return { pollingStore, sessionStore, indicator: screen.getByTestId('connection-indicator') }
    }

    it('ignores session state entirely when no session poll loop is running (regression guard)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = null
      pollingStore.sessionState = 'backoff'
      pollingStore.sessionRetryCount = 5
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('connected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    })

    // Epic #1990 stage 5b (AC3/AC4, decision 5): an archive/no-live-stream view now
    // explicitly calls markSessionArchived() — this sets BOTH wsStore.currentSessionId
    // and sessionConn.state === 'archived', so the header can render a distinct,
    // correct "Archived" state instead of either a stale "Connected" or a spurious
    // "Disconnected" (the bug this closes).
    it('shows archived (not connected, not disconnected) when the session connection is explicitly marked archived', async () => {
      const { pollingStore, sessionStore, indicator } = await setup()
      sessionStore.currentSessionId = 'archived-sess-1'
      pollingStore.currentSessionId = 'archived-sess-1'
      pollingStore.sessionState = 'archived'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('archived')).toBe(true)
      expect(indicator.classList.contains('connected')).toBe(false)
      expect(indicator.classList.contains('disconnected')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Archived')
    })

    it('a broken UI channel wins over "archived" — a real problem is never hidden behind the calm archived state', async () => {
      const { pollingStore, sessionStore, indicator } = await setup()
      sessionStore.currentSessionId = 'archived-sess-1'
      pollingStore.currentSessionId = 'archived-sess-1'
      pollingStore.sessionState = 'archived'
      pollingStore.uiState = 'idle'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('disconnected')).toBe(true)
      expect(indicator.classList.contains('archived')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Disconnected')
    })

    it('shows resyncing when a session poll loop is running an evicted/stream_reset recovery', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionState = 'resyncing'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('resyncing')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Resyncing')
    })

    it('shows reconnecting (not resyncing) when the session channel is in backoff — precedence check', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionState = 'backoff'
      pollingStore.sessionRetryCount = 2
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('reconnecting')).toBe(true)
      expect(indicator.classList.contains('resyncing')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Reconnecting')
    })

    it('a healthy session channel does not override a broken UI channel — UI disconnect still wins', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionState = 'connected'
      pollingStore.uiState = 'backoff'
      pollingStore.uiRetryCount = 3
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('reconnecting')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Reconnecting')
    })

    it('a resyncing session channel does not override an outright-disconnected UI channel — UI disconnect still wins', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionState = 'resyncing'
      pollingStore.uiState = 'idle'
      pollingStore.uiRetryCount = 0
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('disconnected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Disconnected')
    })

    it('shows connected when both channels are healthy (regression guard)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionState = 'connected'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('connected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    })
  })

  // Issue #1989: backendStatus is a Backend-health signal independent of either
  // transport state — it must surface even while both connections stay 'connected'
  // throughout a pure Backend-side outage (Frontend keeps answering polls normally).
  // Epic #1990 stage 5b (decision 1): kept as its own field per StreamConnection
  // instance, checked across both streams since whichever last polled may differ.
  describe('backend_status indicator (#1989, stage 5b)', () => {
    async function setup() {
      const { pinia } = renderWithStores(HeaderRow1)
      const { usePollingStore } = await import('@/stores/polling')
      const pollingStore = usePollingStore(pinia)
      pollingStore.uiState = 'connected'
      pollingStore.uiRetryCount = 0
      await new Promise(r => setTimeout(r, 0))
      return { pollingStore, indicator: screen.getByTestId('connection-indicator') }
    }

    it('shows backend-unreachable, distinct from disconnected/resyncing, when backendStatus is unreachable', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.uiBackendStatus = 'unreachable'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('backend-unreachable')).toBe(true)
      expect(indicator.classList.contains('disconnected')).toBe(false)
      expect(indicator.classList.contains('resyncing')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Backend unreachable')
    })

    it('shows backend-degraded, distinct from backend-unreachable, when backendStatus is degraded', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.uiBackendStatus = 'degraded'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('backend-degraded')).toBe(true)
      expect(indicator.classList.contains('backend-unreachable')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Backend degraded')
    })

    it('surfaces via the SESSION stream\'s own backendStatus field too (decision 1: tracked per-connection, not shared)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.currentSessionId = 'sess-1'
      pollingStore.sessionState = 'connected'
      pollingStore.sessionBackendStatus = 'unreachable'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('backend-unreachable')).toBe(true)
    })

    it('clears back to connected once backendStatus returns to ok (AC4)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.uiBackendStatus = 'unreachable'
      await new Promise(r => setTimeout(r, 0))
      expect(indicator.classList.contains('backend-unreachable')).toBe(true)

      pollingStore.uiBackendStatus = 'ok'
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('connected')).toBe(true)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Connected')
    })

    it('an outright transport disconnect still wins over a Backend outage (severity ranking)', async () => {
      const { pollingStore, indicator } = await setup()
      pollingStore.uiBackendStatus = 'degraded'
      pollingStore.uiState = 'idle'
      pollingStore.uiRetryCount = 0
      await new Promise(r => setTimeout(r, 0))

      expect(indicator.classList.contains('disconnected')).toBe(true)
      expect(indicator.classList.contains('backend-degraded')).toBe(false)
      expect(indicator.getAttribute('aria-label')).toBe('Connection status: Disconnected')
    })
  })
})
