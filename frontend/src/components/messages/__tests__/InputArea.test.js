import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import InputArea from '@/components/messages/InputArea.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn().mockReturnValue(null),
  setAuthToken: vi.fn()
}))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
vi.mock('@/composables/useSessionState', () => ({
  useSessionState: () => ({
    isStarting: { value: false },
    isPaused: { value: false },
    isActive: { value: true },
    isError: { value: false }
  })
}))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('InputArea', () => {
  it('typing updates currentInput in session store', async () => {
    const { pinia } = renderWithStores(InputArea, {
      stubs: {
        AttachmentList: true,
        SlashCommandDropdown: true
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { usePollingStore } = await import('@/stores/polling')
    const sessionStore = useSessionStore(pinia)
    const pollingStore = usePollingStore(pinia)
    sessionStore.currentSessionId = 'sess-1'
    pollingStore.sessionConnected = true
    await nextTick()

    const textarea = screen.getByRole('textbox')
    fireEvent.input(textarea, { target: { value: 'hello' } })
    await nextTick()

    expect(sessionStore.currentInput).toBe('hello')
  })

  it('Send button is disabled when input is empty', async () => {
    const { pinia } = renderWithStores(InputArea, {
      stubs: {
        AttachmentList: true,
        SlashCommandDropdown: true
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { usePollingStore } = await import('@/stores/polling')
    const sessionStore = useSessionStore(pinia)
    const pollingStore = usePollingStore(pinia)
    sessionStore.currentSessionId = 'sess-1'
    sessionStore.currentInput = ''
    pollingStore.sessionConnected = true

    const btn = screen.getByRole('button', { name: /send/i })
    expect(btn).toBeDisabled()
  })

  it('slash command dropdown opens when typing /', async () => {
    const { pinia } = renderWithStores(InputArea, {
      stubs: {
        AttachmentList: true,
        SlashCommandDropdown: { template: '<div role="listbox" aria-label="slash commands" />', props: ['commands', 'filter', 'selectedIndex'] }
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { usePollingStore } = await import('@/stores/polling')
    const sessionStore = useSessionStore(pinia)
    const pollingStore = usePollingStore(pinia)
    sessionStore.currentSessionId = 'sess-1'
    pollingStore.sessionConnected = true
    sessionStore.initData.set('sess-1', { slash_commands: ['memory', 'clear'] })
    // Directly set input to trigger watcher in InputArea
    sessionStore.currentInput = '/me'
    await nextTick()

    expect(screen.getByRole('listbox')).toBeTruthy()
  })
})
