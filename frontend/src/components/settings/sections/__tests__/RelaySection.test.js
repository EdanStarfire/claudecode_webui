import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import RelaySection from '@/components/settings/sections/RelaySection.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const ROUTES = [
  { path: '/settings/session/:sessionId/:section', component: { template: '<div/>' } },
  { path: '/settings/template/:templateId/:section', component: { template: '<div/>' } },
]
const flush = () => new Promise(r => setTimeout(r, 0))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/templates') return Promise.resolve({ templates: [] })
    if (url === '/api/profiles') return Promise.resolve({ profiles: [] })
    if (url === '/api/relays') return Promise.resolve({ relays: [{ relay_id: 'r1', name: 'Slack' }] })
    return Promise.resolve({})
  })
})

async function mountSession(config = {}, overrides = {}) {
  const url = '/settings/session/sess-1/relay'
  const rendered = renderWithStores(RelaySection, { routes: ROUTES, initialRoute: url })
  const { useSessionStore } = await import('@/stores/session')
  const { useSettingsStore } = await import('@/stores/settings')
  const sessionStore = useSessionStore(rendered.pinia)
  sessionStore.sessions.set('sess-1', makeSession({
    session_id: 'sess-1', state: 'active', config, ...overrides,
  }))
  await rendered.router.push(url)
  await flush()
  return { ...rendered, sessionStore, settingsStore: useSettingsStore(rendered.pinia) }
}

describe('RelaySection', () => {
  it('lists the store relays in the selector and shows the session handle block', async () => {
    const { wrapper } = await mountSession({ relay_handle: 'ea' })
    const options = wrapper.findAll('[data-testid="relay-select"] option').map(o => o.text())
    expect(options).toEqual(['None', 'Slack'])
    expect(wrapper.find('[data-testid="relay-handle-block"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="relay-handle-block"] input').element.value).toBe('ea')
  })

  it('hides the handle on the template tier', async () => {
    const url = '/settings/template/t1/relay'
    const rendered = renderWithStores(RelaySection, { routes: ROUTES, initialRoute: url })
    const { useTemplateStore } = await import('@/stores/template')
    useTemplateStore(rendered.pinia).templates?.set?.('t1', { template_id: 't1', name: 'T', config: {} })
    await rendered.router.push(url)
    await flush()
    expect(rendered.wrapper.find('[data-testid="relay-handle-block"]').exists()).toBe(false)
  })

  it('renders the policy count read-only and never submits policies or handle', async () => {
    apiMock.patch.mockResolvedValue({ session: {} })
    const { wrapper, settingsStore } = await mountSession({
      relay_id: 'r1', relay_handle: 'ea', relay_channel_policies: { C0AAAAAAAA: 'auto' },
    })
    expect(wrapper.find('[data-testid="relay-policies-summary"]').text()).toContain('1')
    settingsStore.setField('session:sess-1:relay', 'relay_footer_enabled', false)
    await wrapper.vm.save()
    await flush()
    const sent = apiMock.patch.mock.calls[0][1].config
    expect(sent.relay_footer_enabled).toBe(false)
    expect('relay_channel_policies' in sent).toBe(false)
    expect('relay_handle' in sent).toBe(false)
  })

  it('shows the footer 422 message under the footer field', async () => {
    apiMock.patch.mockRejectedValue(new Error('Footer template must not contain !word syntax'))
    const { wrapper, settingsStore } = await mountSession({ relay_id: 'r1' })
    settingsStore.setField('session:sess-1:relay', 'relay_footer_template', 'hi !deploy')
    await wrapper.vm.save()
    await flush()
    expect(wrapper.find('[data-testid="field-error-relay_footer_template"]').text()).toContain('!word')
  })

  it('saves the handle via the dedicated endpoint and shows a 409 inline', async () => {
    apiMock.put.mockRejectedValueOnce(new Error("relay_handle 'ea' is already in use"))
    const { wrapper } = await mountSession({ relay_handle: 'old' })
    const input = wrapper.find('[data-testid="relay-handle-block"] input')
    await input.setValue('ea')
    await wrapper.find('[data-testid="relay-handle-save"]').trigger('click')
    await flush()
    expect(apiMock.put).toHaveBeenCalledWith('/api/sessions/sess-1/relay/handle', { handle: 'ea' })
    expect(wrapper.find('[data-testid="relay-handle-block"]').text()).toContain('already in use')

    apiMock.put.mockResolvedValueOnce({ relay_handle: 'ea2' })
    await input.setValue('ea2')
    await wrapper.find('[data-testid="relay-handle-save"]').trigger('click')
    await flush()
    expect(wrapper.find('[data-testid="relay-handle-block"]').text()).not.toContain('already in use')
  })

  it('shows the not-attached note and restart chip only when the backend reports the reason', async () => {
    const reason = 'Relay attached: its tools are added when the session next starts. Footer, handle and policy edits apply immediately.'
    const { wrapper } = await mountSession({ relay_id: 'r1' }, { restart_required: [reason] })
    expect(wrapper.find('[data-testid="relay-not-attached-note"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="restart-required-chip"]').exists()).toBe(true)
    const { wrapper: w2 } = await mountSession({ relay_id: 'r1' }, { restart_required: [] })
    expect(w2.find('[data-testid="relay-not-attached-note"]').exists()).toBe(false)
  })
})
