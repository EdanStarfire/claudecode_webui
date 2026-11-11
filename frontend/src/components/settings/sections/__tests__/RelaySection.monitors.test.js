import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import RelaySection from '@/components/settings/sections/RelaySection.vue'
import { makeSession } from '@/test-utils/factories'
import { monitor } from '@/components/relay/__tests__/monitorFixtures'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const ROUTES = [{ path: '/settings/session/:sessionId/:section', component: { template: '<div/>' } }]
const flush = () => new Promise(r => setTimeout(r, 0))
const M = monitor({ id: 'm1', session_id: 'sess-1', channel_label: '#ops' })
let monitorsResponse

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  monitorsResponse = () => Promise.resolve({ monitors: [M], total: 1 })
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/templates') return Promise.resolve({ templates: [] })
    if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
    if (url === '/api/relays') return Promise.resolve({ relays: [{ relay_id: 'r1', name: 'Slack' }] })
    if (url === '/api/sessions/sess-1/relay') {
      return Promise.resolve({ relay_id: 'r1', policy_model: { values: ['a'], target_pattern: '^C' }, policies: {} })
    }
    if (url === '/api/sessions/sess-1/relays/r1/monitors') return monitorsResponse()
    return Promise.resolve({})
  })
})

async function mountSession(config, route = '/settings/session/sess-1/relay') {
  const rendered = renderWithStores(RelaySection, { routes: ROUTES, initialRoute: route })
  const { useSessionStore } = await import('@/stores/session')
  const { useSettingsStore } = await import('@/stores/settings')
  useSessionStore(rendered.pinia).sessions.set('sess-1', makeSession({ session_id: 'sess-1', state: 'active', config }))
  await rendered.router.push(route)
  await flush()
  await flush()
  return { ...rendered, settingsStore: useSettingsStore(rendered.pinia) }
}
const rows = (w) => w.findAll('[data-testid="monitor-row"]')

describe('RelaySection session Monitors block', () => {
  it('is hidden without an attached relay', async () => {
    const { wrapper } = await mountSession({})
    expect(wrapper.find('[data-testid="relay-monitors-block"]').exists()).toBe(false)
  })

  it('renders the session rows from the session-qualified route, without the session column', async () => {
    const { wrapper } = await mountSession({ relay_id: 'r1' })
    const block = wrapper.find('[data-testid="relay-monitors-block"]')
    expect(block.attributes('id')).toBe('monitors')
    expect(rows(wrapper)).toHaveLength(1)
    expect(block.find('[data-testid="monitor-session"]').exists()).toBe(false)
    expect(block.find('[data-testid="monitor-filter-session"]').exists()).toBe(false)
    expect(block.text()).toContain('Applies immediately')
    expect(apiMock.get.mock.calls.some(c => c[0] === '/api/sessions/sess-1/relays/r1/monitors')).toBe(true)
    expect(apiMock.get.mock.calls.some(c => c[0] === '/api/relays/r1/monitors')).toBe(false)
  })

  it('sits below the policy editor and above the handle block', async () => {
    const { wrapper } = await mountSession({ relay_id: 'r1' })
    const order = ['relay-policy-block', 'relay-monitors-block', 'relay-handle-block']
    const html = wrapper.html()
    const idx = order.map(t => html.indexOf(`data-testid="${t}"`))
    expect(idx.every(i => i >= 0)).toBe(true)
    expect([...idx].sort((a, b) => a - b)).toEqual(idx)
  })

  it('pause and delete use the session-qualified routes and never dirty the form', async () => {
    apiMock.patch.mockResolvedValue({ ...M, paused: true, effective_state: 'paused' })
    apiMock.delete.mockResolvedValue({})
    const { wrapper, settingsStore } = await mountSession({ relay_id: 'r1' })
    await wrapper.find('[data-testid="monitor-toggle"]').trigger('click')
    await flush()
    expect(apiMock.patch).toHaveBeenCalledWith('/api/sessions/sess-1/relays/r1/monitors/m1', { paused: true })
    await wrapper.find('[data-testid="monitor-delete"]').trigger('click')
    await wrapper.find('[data-testid="monitor-delete-yes"]').trigger('click')
    await flush()
    expect(apiMock.delete).toHaveBeenCalledWith('/api/sessions/sess-1/relays/r1/monitors/m1')
    expect(settingsStore.dirtyAreas.has('session:sess-1:relay')).toBe(false)
    expect(apiMock.patch.mock.calls.every(c => /\/monitors\//.test(c[0]))).toBe(true) // no config write
  })

  it('a failed pause rolls back with an inline message', async () => {
    apiMock.patch.mockRejectedValue(Object.assign(new Error('boom'), { status: 500 }))
    const { wrapper } = await mountSession({ relay_id: 'r1' })
    await wrapper.find('[data-testid="monitor-toggle"]').trigger('click')
    await flush()
    expect(wrapper.find('[data-testid="monitor-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="monitor-state"]').text()).toBe('active')
  })

  it('shows the plain empty line, and a one-line reason for a 409', async () => {
    monitorsResponse = () => Promise.resolve({ monitors: [], total: 0 })
    let r = await mountSession({ relay_id: 'r1' })
    expect(r.wrapper.find('[data-testid="monitor-empty"]').text()).toBe('No monitors. The agent creates them with its monitor tools.')
    monitorsResponse = () => Promise.reject(Object.assign(new Error('x'), { status: 409 }))
    r = await mountSession({ relay_id: 'r1' })
    expect(r.wrapper.find('[data-testid="monitor-not-running"]').text()).toContain('unavailable')
    expect(r.wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('a monitor action leaves unsaved form edits untouched', async () => {
    apiMock.patch.mockResolvedValue({ ...M, paused: true, effective_state: 'paused' })
    const { wrapper, settingsStore } = await mountSession({ relay_id: 'r1' })
    settingsStore.setField('session:sess-1:relay', 'relay_footer_enabled', false)
    await flush()
    await wrapper.find('[data-testid="monitor-toggle"]').trigger('click')
    await flush()
    expect(settingsStore.dirtyAreas.has('session:sess-1:relay')).toBe(true)
    expect(settingsStore.getDraft('session:sess-1:relay')).toEqual({ relay_footer_enabled: false })
  })

  it('scrolls to the block for the #monitors anchor', async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    await mountSession({ relay_id: 'r1' }, '/settings/session/sess-1/relay#monitors')
    expect(scroll).toHaveBeenCalled()
  })
})
