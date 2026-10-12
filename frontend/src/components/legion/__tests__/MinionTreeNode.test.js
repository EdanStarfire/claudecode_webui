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

describe('MinionTreeNode - filter dimming (issue #2050, Stage B)', () => {
  it('applies filter-dimmed when _filterDimmed is true', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [], _filterDimmed: true }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    useSessionStore(pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))

    expect(wrapper.find('.minion-card').classes()).toContain('filter-dimmed')
  })

  it('omits filter-dimmed when _filterDimmed is false or undefined', async () => {
    const { useSessionStore } = await import('@/stores/session')

    const notMatched = mountNode({ id: 'sess-1', name: 'Minion One', children: [], _filterDimmed: false })
    useSessionStore(notMatched.pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))
    expect(notMatched.wrapper.find('.minion-card').classes()).not.toContain('filter-dimmed')

    const unfiltered = mountNode({ id: 'sess-2', name: 'Minion Two', children: [] })
    useSessionStore(unfiltered.pinia).sessions.set('sess-2', makeSession({ session_id: 'sess-2', name: 'Minion Two' }))
    expect(unfiltered.wrapper.find('.minion-card').classes()).not.toContain('filter-dimmed')
  })

  it('stays clickable and emits minion-click regardless of dimmed state', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [], _filterDimmed: true }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    useSessionStore(pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))

    await fireEvent.click(wrapper.find('.minion-card').element)
    expect(wrapper.emitted('minion-click')).toBeTruthy()
    expect(wrapper.emitted('minion-click')[0]).toEqual(['sess-1'])
  })

  it('renders children of a user-collapsed node when filtering is active, even without an active descendant', async () => {
    const minionData = {
      id: 'parent',
      name: 'Parent',
      _filterDimmed: true,
      children: [{ id: 'child', name: 'Child', children: [], _filterDimmed: false }],
    }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('parent', makeSession({ session_id: 'parent', name: 'Parent' }))
    sessionStore.sessions.set('child', makeSession({ session_id: 'child', name: 'Child' }))

    useUIStore(pinia).toggleMinionCollapse('parent')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.minion-tree-node')).toHaveLength(2)
    expect(wrapper.text()).toContain('Child')
  })

  it('keeps the collapse chevron in sync when filtering forces a collapsed branch open', async () => {
    const minionData = {
      id: 'parent',
      name: 'Parent',
      _filterDimmed: true,
      children: [{ id: 'child', name: 'Child', children: [], _filterDimmed: false }],
    }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('parent', makeSession({ session_id: 'parent', name: 'Parent' }))
    sessionStore.sessions.set('child', makeSession({ session_id: 'child', name: 'Child' }))

    useUIStore(pinia).toggleMinionCollapse('parent')
    await wrapper.vm.$nextTick()

    const toggle = wrapper.find('.collapse-toggle')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(toggle.attributes('aria-label')).toBe('Collapse branch')
    expect(wrapper.find('.tree-chevron').classes()).toContain('tree-chevron-open')
  })

  it('respects collapse when no filter is active', async () => {
    const minionData = {
      id: 'parent',
      name: 'Parent',
      children: [{ id: 'child', name: 'Child', children: [] }],
    }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('parent', makeSession({ session_id: 'parent', name: 'Parent' }))
    sessionStore.sessions.set('child', makeSession({ session_id: 'child', name: 'Child' }))

    useUIStore(pinia).toggleMinionCollapse('parent')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.minion-tree-node')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Child')
  })
})

describe('MinionTreeNode - compact row layout (issue #2061)', () => {
  it('shows name + role on one line (em-dash separated) in compact mode, hiding the stacked role subtitle', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    useSessionStore(pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One', role: 'Researcher' }))
    useUIStore(pinia).setCompactMinionTreeRows(true)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.node-row').classes()).toContain('compact')
    expect(wrapper.find('.minion-card').classes()).toContain('compact')
    expect(wrapper.find('.node-role').exists()).toBe(false)
    expect(wrapper.find('.node-role-inline').exists()).toBe(true)
    expect(wrapper.find('.node-role-inline').text()).toBe('— Researcher')
  })

  it('omits the inline role separator entirely when no role is present (no dangling em dash)', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    useSessionStore(pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One' }))
    useUIStore(pinia).setCompactMinionTreeRows(true)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.node-role-inline').exists()).toBe(false)
  })

  it('omits the inline role separator for a whitespace-only role (no dangling em dash)', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    useSessionStore(pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One', role: '   ' }))
    useUIStore(pinia).setCompactMinionTreeRows(true)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.node-role-inline').exists()).toBe(false)
  })

  it('still renders the role subtitle in normal mode (regression guard)', async () => {
    const minionData = { id: 'sess-1', name: 'Minion One', children: [] }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    useSessionStore(pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', name: 'Minion One', role: 'Researcher' }))
    useUIStore(pinia).setCompactMinionTreeRows(false)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.node-row').classes()).not.toContain('compact')
    expect(wrapper.find('.node-role').exists()).toBe(true)
    expect(wrapper.find('.node-role').text()).toBe('Researcher')
  })

  it('status-dot batch-select and collapse-chevron handlers still fire in compact mode', async () => {
    const minionData = {
      id: 'parent',
      name: 'Parent',
      children: [{ id: 'child', name: 'Child', children: [] }],
    }
    const { pinia, wrapper } = mountNode(minionData)
    const { useSessionStore } = await import('@/stores/session')
    const { useUIStore } = await import('@/stores/ui')
    const sessionStore = useSessionStore(pinia)
    const uiStore = useUIStore(pinia)
    sessionStore.sessions.set('parent', makeSession({ session_id: 'parent', name: 'Parent' }))
    sessionStore.sessions.set('child', makeSession({ session_id: 'child', name: 'Child' }))
    uiStore.setCompactMinionTreeRows(true)
    await wrapper.vm.$nextTick()

    const dot = wrapper.find('.status-dot')
    await fireEvent.click(dot.element)
    expect(uiStore.isSessionSelected('parent')).toBe(true)

    const toggle = wrapper.find('.collapse-toggle')
    await fireEvent.click(toggle.element)
    expect(uiStore.isMinionCollapsed('parent')).toBe(true)
  })
})
