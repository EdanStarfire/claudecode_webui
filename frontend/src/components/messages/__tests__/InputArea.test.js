import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick, ref } from 'vue'
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

const SESSION_ID = 'sess-1'
const viewSessionIdRef = ref(SESSION_ID)

beforeEach(() => {
  setActivePinia(createPinia())
  viewSessionIdRef.value = SESSION_ID
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('InputArea', () => {
  it('typing updates input in session store', async () => {
    const { pinia } = renderWithStores(InputArea, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        AttachmentList: true,
        SlashCommandDropdown: true
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { usePollingStore } = await import('@/stores/polling')
    const sessionStore = useSessionStore(pinia)
    const pollingStore = usePollingStore(pinia)
    sessionStore.currentSessionId = SESSION_ID
    pollingStore.sessionConnected = true
    await nextTick()

    const textarea = screen.getByRole('textbox')
    fireEvent.input(textarea, { target: { value: 'hello' } })
    await nextTick()

    expect(sessionStore.getInput(SESSION_ID)).toBe('hello')
  })

  it('Send button is disabled when input is empty', async () => {
    const { pinia } = renderWithStores(InputArea, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        AttachmentList: true,
        SlashCommandDropdown: true
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { usePollingStore } = await import('@/stores/polling')
    const sessionStore = useSessionStore(pinia)
    const pollingStore = usePollingStore(pinia)
    sessionStore.currentSessionId = SESSION_ID
    sessionStore.setInput(SESSION_ID, '')
    pollingStore.sessionConnected = true

    const btn = screen.getByRole('button', { name: /send/i })
    expect(btn).toBeDisabled()
  })

  it('slash command dropdown opens when typing /', async () => {
    const { pinia } = renderWithStores(InputArea, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        AttachmentList: true,
        SlashCommandDropdown: { template: '<div role="listbox" aria-label="slash commands" />', props: ['commands', 'filter', 'selectedIndex'] }
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { usePollingStore } = await import('@/stores/polling')
    const sessionStore = useSessionStore(pinia)
    const pollingStore = usePollingStore(pinia)
    sessionStore.currentSessionId = SESSION_ID
    pollingStore.sessionConnected = true
    sessionStore.initData.set(SESSION_ID, { slash_commands: ['memory', 'clear'] })
    sessionStore.setInput(SESSION_ID, '/me')
    await nextTick()

    expect(screen.getByRole('listbox')).toBeTruthy()
  })
})
