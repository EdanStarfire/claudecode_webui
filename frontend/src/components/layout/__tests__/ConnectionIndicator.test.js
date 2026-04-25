import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ConnectionIndicator from '@/components/layout/ConnectionIndicator.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('ConnectionIndicator', () => {
  it('reflects polling store connected/disconnected state via aria-label', async () => {
    const { pinia } = renderWithStores(ConnectionIndicator)

    const { usePollingStore } = await import('@/stores/polling')
    const pollingStore = usePollingStore(pinia)

    pollingStore.uiConnected = true
    await new Promise(r => setTimeout(r, 0))

    const uiIndicator = screen.getByRole('status', { name: /ui poll/i })
    expect(uiIndicator.getAttribute('aria-label')).toContain('Connected')

    pollingStore.uiConnected = false
    pollingStore.uiRetryCount = 0
    await new Promise(r => setTimeout(r, 0))

    expect(uiIndicator.getAttribute('aria-label')).toContain('Disconnected')
  })
})
