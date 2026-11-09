import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { vi } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import LibraryRelaysSection from '@/components/settings/sections/LibraryRelaysSection.vue'
import { useSettingsStore } from '@/stores/settings'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const flush = () => new Promise(r => setTimeout(r, 0))
const REC = {
  relay_id: 'r1', name: 'Slack', adapter_type: 'slack', has_user_token: true, has_app_token: true,
  inbound_enabled: true, inbound_status: 'connected', inbound_detail: null,
}

function mockApi(record) {
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/relays') return Promise.resolve({ relays: [record] })
    if (url === '/api/secrets') return Promise.resolve({ secrets: [] })
    if (url.includes('/inbound/status')) {
      return Promise.resolve({ status: record.inbound_status, detail: record.inbound_detail, enabled: record.inbound_enabled })
    }
    return Promise.resolve({ manifest: {}, manifest_json: '{}', steps: [], scopes: { required: [], optional: [], inbound: [] } })
  })
}

describe('Relays inbound status chip and toggle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
  })

  it.each([
    ['connected', null, 'connected'],
    ['backoff', null, 'reconnecting'],
    ['connecting', null, 'connecting'],
    ['transport_unavailable', 'slack_sdk_missing', 'unavailable'],
    ['auth_failed', 'token_revoked', 'auth failed'],
    ['disabled', 'no_app_token', 'off'],
  ])('renders %s as "%s" with a tooltip', async (status, detail, label) => {
    mockApi({ ...REC, inbound_status: status, inbound_detail: detail })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    const chip = wrapper.find('[data-testid="relay-inbound-chip"]')
    expect(chip.text()).toBe(label)
    expect(chip.attributes('title')).toContain('one Backend per Slack app-level token')
    if (detail) expect(chip.attributes('title')).toContain(detail)
  })

  it('owner-disabled shows off whatever the live status', async () => {
    mockApi({ ...REC, inbound_enabled: false, inbound_status: 'connected' })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('[data-testid="relay-inbound-chip"]').text()).toBe('off')
    expect(wrapper.find('[data-testid="relay-inbound-checkbox"]').element.checked).toBe(false)
  })

  it('toggle is saved by the single Save, marks the area dirty and clears it after saving', async () => {
    mockApi(REC)
    apiMock.put.mockResolvedValue({ ...REC, inbound_enabled: false })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    const settings = useSettingsStore()
    expect(settings.hasDirtyAreas).toBe(false)
    expect(wrapper.find('[data-testid="relay-secrets-save"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="relay-inbound-checkbox"]').setValue(false)
    await flush()
    expect(settings.hasDirtyAreas).toBe(true)  // the navigation guard sees it
    await wrapper.find('[data-testid="relay-secret-pickers"]').trigger('submit')
    await flush()
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1', { inbound_enabled: false })
    expect(apiMock.put).not.toHaveBeenCalledWith('/api/relays/r1/secrets', expect.anything())
    expect(settings.hasDirtyAreas).toBe(false)
  })

  it('exposes save() for the dirty-guard Apply', async () => {
    mockApi(REC)
    apiMock.put.mockResolvedValue({ ...REC, inbound_enabled: false })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    await wrapper.find('[data-testid="relay-inbound-checkbox"]').setValue(false)
    await flush()
    await wrapper.vm.save()
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1', { inbound_enabled: false })
  })

  it('keeps the card quiet: no warning boxes for inbound states', async () => {
    mockApi({ ...REC, inbound_status: 'auth_failed', inbound_detail: 'token_revoked' })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('.alert').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-inbound-toggle"]').element.children.length).toBeLessThanOrEqual(3)
  })
})
