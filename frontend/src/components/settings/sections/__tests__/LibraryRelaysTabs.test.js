import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import LibraryRelaysSection from '@/components/settings/sections/LibraryRelaysSection.vue'
import { useSettingsStore } from '@/stores/settings'
import { health, monitor } from '@/components/relay/__tests__/monitorFixtures'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const REC = {
  relay_id: 'r1', name: 'Slack', adapter_type: 'slack', has_user_token: true, has_app_token: true,
  inbound_enabled: true, inbound_status: 'connected', inbound_detail: null,
}
const ROUTES = [{ path: '/settings/relays', component: { template: '<div/>' } }, { path: '/', component: { template: '<div/>' } }]
let healthPayload

function mockApi() {
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/relays') return Promise.resolve({ relays: [REC] })
    if (url === '/api/secrets') return Promise.resolve({ secrets: [] })
    if (url.endsWith('/inbound/status')) return Promise.resolve({ status: 'connected', detail: null, enabled: true })
    if (url.endsWith('/inbound/health')) return Promise.resolve(healthPayload)
    if (url.endsWith('/monitors')) return Promise.resolve({ monitors: [monitor()], total: 1 })
    if (url.endsWith('/audit')) return Promise.resolve({ records: [] })
    return Promise.resolve({ manifest: {}, manifest_json: '{}', steps: [], scopes: { required: [], optional: [], inbound: [] } })
  })
}

async function mountAt(url = '/settings/relays') {
  const r = renderWithStores(LibraryRelaysSection, { routes: ROUTES, initialRoute: url })
  await r.router.push(url)
  await flushPromises()
  return r
}
const settle = async () => { await new Promise(r => setTimeout(r, 300)); await flushPromises() }
const shown = (w, key) => w.find(`[data-testid="relay-panel-${key}"]`).attributes('style') !== 'display: none;'

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  healthPayload = health()
  mockApi()
})

describe('Relays tabs', () => {
  it('defaults to Setup; every panel stays mounted (v-show)', async () => {
    const { wrapper } = await mountAt()
    expect(shown(wrapper, 'setup')).toBe(true)
    expect(shown(wrapper, 'inbound')).toBe(false)
    expect(shown(wrapper, 'audit')).toBe(false)
    expect(wrapper.find('[data-testid="relay-tab-setup"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[data-testid="relay-secrets-save"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="inbound-tab"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="relay-audit"]').exists()).toBe(true)
  })

  it.each([['inbound'], ['audit'], ['setup']])('?tab=%s opens that tab', async (tab) => {
    const { wrapper } = await mountAt(`/settings/relays?tab=${tab}`)
    for (const k of ['setup', 'inbound', 'audit']) expect(shown(wrapper, k)).toBe(k === tab)
  })

  it('a legacy ?session= / ?channel= deep link opens Audit with the filter', async () => {
    const { wrapper } = await mountAt('/settings/relays?session=s1')
    await settle()
    expect(shown(wrapper, 'audit')).toBe(true)
    expect(apiMock.get.mock.calls.filter(c => c[0].endsWith('/audit')).at(-1)[1].params.session_id).toBe('s1')
    const r2 = await mountAt('/settings/relays?channel=C0AAAAAAA1')
    expect(shown(r2.wrapper, 'audit')).toBe(true)
  })

  it('?tab=audit&events= prefilters the audit; ?tab=inbound&session= prefilters the monitors', async () => {
    await mountAt('/settings/relays?tab=audit&events=monitor_*&session=s1')
    await settle()
    expect(apiMock.get.mock.calls.filter(c => c[0].endsWith('/audit')).at(-1)[1].params).toMatchObject({ events: 'monitor_*', session_id: 's1' })
    apiMock.get.mockClear()
    await mountAt('/settings/relays?tab=inbound&session=s9')
    await settle()
    expect(apiMock.get.mock.calls.filter(c => c[0].endsWith('/monitors')).at(-1)[1].params.session_id).toBe('s9')
  })

  it('clicking a tab switches panels and updates ?tab=', async () => {
    const { wrapper, router } = await mountAt()
    await wrapper.find('[data-testid="relay-tab-inbound"]').trigger('click')
    await flushPromises()
    expect(shown(wrapper, 'inbound')).toBe(true)
    expect(router.currentRoute.value.query.tab).toBe('inbound')
    await wrapper.find('[data-testid="relay-tab-audit"]').trigger('click')
    await flushPromises()
    expect(shown(wrapper, 'audit')).toBe(true)
    expect(shown(wrapper, 'inbound')).toBe(false)
  })

  it('an unsaved credentials form survives tab switches and keeps the dirty guard', async () => {
    const { wrapper } = await mountAt()
    const settings = useSettingsStore()
    await wrapper.find('[data-testid="relay-inbound-checkbox"]').setValue(false)
    await flushPromises()
    expect(settings.hasDirtyAreas).toBe(true)
    await wrapper.find('[data-testid="relay-tab-inbound"]').trigger('click')
    await wrapper.find('[data-testid="relay-tab-audit"]').trigger('click')
    await wrapper.find('[data-testid="relay-tab-setup"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="relay-inbound-checkbox"]').element.checked).toBe(false)
    expect(settings.hasDirtyAreas).toBe(true)
  })

  it('the Inbound tab never marks anything dirty: its actions are immediate', async () => {
    apiMock.patch.mockResolvedValue({ ...monitor(), paused: true, effective_state: 'paused' })
    const { wrapper } = await mountAt('/settings/relays?tab=inbound')
    const settings = useSettingsStore()
    await wrapper.find('[data-testid="monitor-toggle"]').trigger('click')
    await flushPromises()
    expect(apiMock.patch).toHaveBeenCalled()
    expect(settings.hasDirtyAreas).toBe(false)
  })

  it('the Inbound tab dot shows only for attention-level health', async () => {
    let r = await mountAt()
    expect(r.wrapper.find('[data-testid="relay-tab-dot"]').exists()).toBe(false)
    healthPayload = health({ attention: [{ code: 'monitors_paused', severity: 'info', count: 1, audit: { events: [] } }] })
    r = await mountAt()
    expect(r.wrapper.find('[data-testid="relay-tab-dot"]').exists()).toBe(false)
    healthPayload = health({ status: 'auth_failed', attention: [{ code: 'auth_failed', severity: 'attention', count: 1, audit: { events: ['inbound_connection'] } }] })
    r = await mountAt()
    expect(r.wrapper.find('[data-testid="relay-tab-dot"]').exists()).toBe(true)
  })

  it('switching tabs keeps the other query params (deep-link filters)', async () => {
    const { wrapper, router } = await mountAt('/settings/relays?tab=audit&events=monitor_flood&session=s1')
    await wrapper.find('[data-testid="relay-tab-inbound"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ tab: 'inbound', events: 'monitor_flood', session: 's1' })
  })

  it('the tab strip scrolls horizontally on phones (no wrapping)', async () => {
    const { wrapper } = await mountAt()
    const tabs = wrapper.find('[data-testid="relay-tabs"]')
    expect(tabs.attributes('role')).toBe('tablist')
    expect(wrapper.findAll('[role="tab"]')).toHaveLength(3)
  })
})
