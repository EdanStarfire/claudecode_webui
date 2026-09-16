import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import MinionTreeNode from '@/components/legion/MinionTreeNode.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

function mountNode(minionData) {
  return renderWithStores(MinionTreeNode, {
    props: { minionData, level: 0 },
    stubs: { SessionActionsMenu: true }
  })
}

describe('MinionTreeNode - status dot batch selection (issue #1934)', () => {
  it('clicking the status dot toggles uiStore.selectedSessionIds and does not emit minion-click', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    const uiStore = useUIStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))

    expect(uiStore.isSessionSelected('sess-1')).toBe(false)

    const dot = wrapper.find('.status-dot')
    await fireEvent.click(dot.element)

    expect(uiStore.isSessionSelected('sess-1')).toBe(true)
    expect(wrapper.emitted('minion-click')).toBeFalsy()

    // Clicking again deselects
    await fireEvent.click(dot.element)
    expect(uiStore.isSessionSelected('sess-1')).toBe(false)
  })

  it('renders the is-selected class only while selected', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))

    const dot = wrapper.find('.status-dot')
    expect(dot.classes()).not.toContain('is-selected')

    await fireEvent.click(dot.element)
    expect(wrapper.find('.status-dot').classes()).toContain('is-selected')
  })

  it('clicking elsewhere on the card still emits minion-click and does not touch selection', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    const uiStore = useUIStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))

    const card = wrapper.find('.minion-card')
    await fireEvent.click(card.element)

    expect(wrapper.emitted('minion-click')).toBeTruthy()
    expect(wrapper.emitted('minion-click')[0]).toEqual(['sess-1'])
    expect(uiStore.isSessionSelected('sess-1')).toBe(false)
  })
})
