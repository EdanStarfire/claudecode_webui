import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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
// Issue #1746 (stage: permissions) follow-up: must be real Vue refs, not plain {value} objects —
// Vue's template auto-unwrap (e.g. `:disabled="... || isStarting || ..."` in InputArea.vue) only
// unwraps genuine refs. A plain object is always truthy in that position regardless of its own
// .value, which previously made the Send button appear permanently disabled in any test that
// exercised it (masked until now because no prior test asserted the enabled-button path).
vi.mock('@/composables/useSessionState', () => ({
  useSessionState: () => ({
    isStarting: ref(false),
    isPaused: ref(false),
    isActive: ref(true),
    isError: ref(false)
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

  it('a rejected send keeps the draft and shows an error, instead of silently discarding it (#1746 follow-up)', async () => {
    const user = userEvent.setup()
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

    apiMock.post.mockRejectedValue(
      Object.assign(new Error('Session is not active'), { status: 409, data: { detail: 'Session is not active' } })
    )

    const textarea = screen.getByRole('textbox')
    fireEvent.update(textarea, 'draft message')
    await nextTick()
    const sendBtn = screen.getByRole('button', { name: /^send$/i })
    await user.click(sendBtn)
    await new Promise(r => setTimeout(r, 0))
    await nextTick()

    expect(sessionStore.getInput(SESSION_ID)).toBe('draft message')
    expect(screen.getByText('Session is not active')).toBeTruthy()
  })

  it('a successful send clears the draft', async () => {
    const user = userEvent.setup()
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

    apiMock.post.mockResolvedValue({})

    const textarea = screen.getByRole('textbox')
    fireEvent.update(textarea, 'a real message')
    await nextTick()
    const sendBtn = screen.getByRole('button', { name: /^send$/i })
    await user.click(sendBtn)
    await new Promise(r => setTimeout(r, 0))
    await nextTick()

    expect(sessionStore.getInput(SESSION_ID)).toBe('')
  })

  // Issue #1788: measurement moved from the live textarea to an offscreen clone to
  // avoid forcing layout on large unvirtualized sessions. jsdom implements neither
  // ResizeObserver nor real layout (scrollHeight is always 0), so these tests assert
  // structural wiring (clone lifecycle, observe/disconnect calls) rather than pixel
  // heights.
  describe('offscreen resize clone (#1788)', () => {
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it('appends a hidden measurement clone on mount and removes it on unmount', async () => {
      const { pinia, wrapper } = renderWithStores(InputArea, {
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

      const clones = document.body.querySelectorAll('textarea[aria-hidden="true"]')
      expect(clones.length).toBe(1)

      wrapper.unmount()

      expect(document.body.querySelectorAll('textarea[aria-hidden="true"]').length).toBe(0)
    })

    it('observes the real textarea with ResizeObserver on mount and disconnects on unmount, when available', async () => {
      const observeMock = vi.fn()
      const disconnectMock = vi.fn()
      class MockResizeObserver {
        constructor(callback) { this.callback = callback }
        observe = observeMock
        disconnect = disconnectMock
        unobserve = vi.fn()
      }
      vi.stubGlobal('ResizeObserver', MockResizeObserver)

      const { pinia, wrapper } = renderWithStores(InputArea, {
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
      expect(observeMock).toHaveBeenCalledWith(textarea)

      wrapper.unmount()

      expect(disconnectMock).toHaveBeenCalled()
    })

    it('mounts and unmounts without error when ResizeObserver is unavailable (default jsdom env)', async () => {
      expect(typeof globalThis.ResizeObserver).toBe('undefined')

      const { pinia, wrapper } = renderWithStores(InputArea, {
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

      // The measurement clone still gets created regardless of ResizeObserver support.
      expect(document.body.querySelectorAll('textarea[aria-hidden="true"]').length).toBe(1)

      expect(() => wrapper.unmount()).not.toThrow()
    })
  })
})
