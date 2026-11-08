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

  it('mounts the live policy editor and never submits policies or handle', async () => {
    apiMock.patch.mockResolvedValue({ session: {} })
    const base = apiMock.get.getMockImplementation()
    apiMock.get.mockImplementation((url) => url === '/api/sessions/sess-1/relay'
      ? Promise.resolve({
        relay_id: 'r1', policy_model: { values: ['a', 'b'], target_pattern: '^C[0-9A-Z]+$' },
        policies: { C0AAAAAAAA: { policy: 'a', source: 'session' } },
      })
      : base(url))
    const { wrapper, settingsStore } = await mountSession({
      relay_id: 'r1', relay_handle: 'ea', relay_channel_policies: { r1: { C0AAAAAAAA: 'auto' } },
    })
    expect(wrapper.find('[data-testid="relay-policy-editor"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="relay-policy-row-C0AAAAAAAA"]').exists()).toBe(true)
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


describe('RelaySection policy editor on template and profile tiers', () => {
  const MODEL = { values: ['blocked', 'ask'], target_pattern: '^C[0-9A-Z]{6,}$', target_label: 'Slack channel ID' }

  async function mountTemplate(config, profileConfig = null) {
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/relays') return Promise.resolve({ relays: [{ relay_id: 'r1', name: 'Slack' }] })
      if (url === '/api/relays/r1/policy-model') return Promise.resolve(MODEL)
      if (url.includes('/conversations')) return Promise.resolve({ labels: {} })
      return Promise.resolve({ templates: [], profiles: [] })
    })
    const url = '/settings/template/t1/relay'
    const rendered = renderWithStores(RelaySection, { routes: ROUTES, initialRoute: url })
    const { useTemplateStore } = await import('@/stores/template')
    const { useProfileStore } = await import('@/stores/profile')
    const { useSettingsStore } = await import('@/stores/settings')
    useTemplateStore(rendered.pinia).templates?.set?.('t1', {
      template_id: 't1', name: 'T', config, profile_ids: profileConfig ? { relay: 'p1' } : {},
    })
    if (profileConfig) {
      useProfileStore(rendered.pinia).profiles?.set?.('p1', { profile_id: 'p1', name: 'P', area: 'relay', config: profileConfig })
    }
    await rendered.router.push(url)
    await flush()
    await flush()
    return { ...rendered, settingsStore: useSettingsStore(rendered.pinia) }
  }

  it('emits only own-tier nested entries into the draft, never inherited keys', async () => {
    const { wrapper, settingsStore } = await mountTemplate(
      { relay_id: 'r1', relay_channel_policies: { r1: { C0OWNOWN01: 'ask' } } },
      { relay_channel_policies: { r1: { C0FROMPROF1: 'blocked' } } },
    )
    expect(wrapper.find('[data-testid="relay-policy-row-C0FROMPROF1"] [data-testid="relay-policy-tier"]').text()).toBe('P')
    expect(wrapper.find('[data-testid="relay-policy-row-C0OWNOWN01"] [data-testid="relay-policy-tier"]').text()).toBe('S')
    await wrapper.find('[data-testid="relay-policy-row-C0OWNOWN01"] [data-testid="relay-policy-opt-blocked"]').trigger('click')
    expect(settingsStore.getDraft('template:t1:relay').relay_channel_policies)
      .toEqual({ r1: { C0OWNOWN01: 'blocked' } })
  })

  it('overriding an inherited row writes an own entry for that target only', async () => {
    const { wrapper, settingsStore } = await mountTemplate(
      { relay_id: 'r1' }, { relay_channel_policies: { r1: { C0FROMPROF1: 'blocked' } } })
    await wrapper.find('[data-testid="relay-policy-row-C0FROMPROF1"] [data-testid="relay-policy-opt-ask"]').trigger('click')
    expect(settingsStore.getDraft('template:t1:relay').relay_channel_policies).toEqual({ r1: { C0FROMPROF1: 'ask' } })
  })

  it('a legacy flat stored dict is read under the sole relay and written back nested', async () => {
    const { wrapper, settingsStore } = await mountTemplate(
      { relay_id: 'r1', relay_channel_policies: { C0LEGACY001: 'ask' } })
    await wrapper.find('[data-testid="relay-policy-row-C0LEGACY001"] [data-testid="relay-policy-remove"]').trigger('click')
    expect(settingsStore.getDraft('template:t1:relay').relay_channel_policies).toEqual({})
  })
})
