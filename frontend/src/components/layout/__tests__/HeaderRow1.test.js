import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import HeaderRow1 from '@/components/layout/HeaderRow1.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
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
})
