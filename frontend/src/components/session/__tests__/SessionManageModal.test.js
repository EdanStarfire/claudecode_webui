import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import SessionManageModal from '@/components/session/SessionManageModal.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

// jsdom doesn't implement the DOM/CSS transition machinery bootstrap's real Modal
// relies on (focus trap, backdrop, transitionend) — stub it so this test exercises
// only our own click handler, not bootstrap's show/hide lifecycle.
vi.mock('bootstrap/js/dist/modal', () => ({
  default: class ModalStub {
    show() {}
    hide() {}
    dispose() {}
  }
}))

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ has_history: false, has_archives: false })
})

describe('SessionManageModal — New Session From This Configuration (issue #2164)', () => {
  it('refreshes the session then navigates with project_id + seed_session_id query params', async () => {
    const { pinia, router } = renderWithStores(SessionManageModal, {
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/settings/session/:sessionId/general', component: { template: '<div />' } },
      ],
    })
    await flush() // let the dynamic bootstrap Modal import resolve

    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    const uiStore = useUIStore(pinia)
    const session = makeSession({ session_id: 'sess-1', project_id: 'proj-1' })
    sessionStore.sessions.set('sess-1', session)
    const fetchSpy = vi.spyOn(sessionStore, 'fetchSessionDetails').mockResolvedValue({})

    uiStore.showModal('manage-session', { session })
    await flush()

    await fireEvent.click(screen.getByText(/new session from this configuration/i))
    await flush()

    expect(fetchSpy).toHaveBeenCalledWith('sess-1')
    expect(router.currentRoute.value.path).toBe('/settings/session/__new__/general')
    expect(router.currentRoute.value.query).toEqual({ project_id: 'proj-1', seed_session_id: 'sess-1' })
  })
})
