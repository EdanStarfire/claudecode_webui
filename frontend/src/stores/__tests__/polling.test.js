import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

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
})
