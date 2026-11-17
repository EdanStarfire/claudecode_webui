import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import LibraryRelaysSection from '@/components/settings/sections/LibraryRelaysSection.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const flush = () => new Promise(r => setTimeout(r, 0))
const REC = {
  relay_id: 'r1', name: 'Slack', adapter_type: 'slack', has_user_token: true, has_app_token: false,
  inbound_enabled: true, default_footer_enabled: null, default_footer_template: null,
}

function mockApi(rec) {
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/relays') return Promise.resolve({ relays: [rec] })
    if (url.includes('/manifest')) return Promise.resolve({ manifest: {}, manifest_json: '{}', steps: [], scopes: { required: [], optional: [], inbound: [] } })
    if (url === '/api/relays/r1') return Promise.resolve(rec)
    return Promise.resolve({})
  })
}

async function mountCard(rec = REC) {
  mockApi(rec)
  const rendered = renderWithStores(LibraryRelaysSection)
  await flush()
  return rendered
}

describe('Library Relays: Default footer block', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
  })

  it('maps the tri-state to null / true / false and hides the text when Off', async () => {
    const { wrapper } = await mountCard({ ...REC, default_footer_enabled: false })
    expect(wrapper.find('[data-testid="relay-footer-mode"]').element.value).toBe('off')
    expect(wrapper.find('[data-testid="relay-footer-text"]').exists()).toBe(false)
    const rec2 = await mountCard({ ...REC, default_footer_enabled: true })
    expect(rec2.wrapper.find('[data-testid="relay-footer-mode"]').element.value).toBe('on')
    const rec3 = await mountCard(REC)
    expect(rec3.wrapper.find('[data-testid="relay-footer-mode"]').element.value).toBe('inherit')
  })

  it('shows the counter, the built-in placeholder and the no-force hint', async () => {
    const { wrapper } = await mountCard()
    const text = wrapper.find('[data-testid="relay-footer-text"]')
    expect(text.attributes('placeholder')).toContain('{session_name}')
    await text.setValue('Hello')
    expect(wrapper.find('[data-testid="relay-footer-counter"]').text()).toBe('5 / 500')
    expect(wrapper.find('[data-testid="relay-default-footer"]').text()).toContain('by writing it out itself')
  })

  it('over-length text is flagged inline and cannot be saved', async () => {
    const { wrapper } = await mountCard()
    await wrapper.find('[data-testid="relay-footer-text"]').setValue('x'.repeat(501))
    expect(wrapper.find('[data-testid="relay-footer-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="relay-secrets-save"]').attributes('disabled')).toBeDefined()
  })

  it('saves only the changed footer fields through the single Save and clears the dirty guard', async () => {
    const { wrapper } = await mountCard()
    const { useSettingsStore } = await import('@/stores/settings')
    const settings = useSettingsStore()
    apiMock.put.mockResolvedValue({ ...REC, default_footer_enabled: true, default_footer_template: 'Hi {session_name}' })
    await wrapper.find('[data-testid="relay-footer-mode"]').setValue('on')
    await wrapper.find('[data-testid="relay-footer-text"]').setValue('Hi {session_name}')
    await flush()
    expect(settings.hasDirtyAreas).toBe(true)
    await wrapper.find('[data-testid="relay-secret-pickers"]').trigger('submit')
    await flush()
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1', {
      default_footer_enabled: true, default_footer_template: 'Hi {session_name}',
    })
    expect(settings.hasDirtyAreas).toBe(false)
  })

  it('empty text is sent as null', async () => {
    const { wrapper } = await mountCard({ ...REC, default_footer_template: 'Old' })
    apiMock.put.mockResolvedValue(REC)
    await wrapper.find('[data-testid="relay-footer-text"]').setValue('  ')
    await wrapper.find('[data-testid="relay-secret-pickers"]').trigger('submit')
    await flush()
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1', { default_footer_template: null })
  })

  it('shows a server rejection inline and keeps the form dirty', async () => {
    const { wrapper } = await mountCard()
    apiMock.put.mockRejectedValue(new Error('relay_footer_template must not contain !handle trigger syntax'))
    await wrapper.find('[data-testid="relay-footer-text"]').setValue('hi !deploy')
    await wrapper.find('[data-testid="relay-secret-pickers"]').trigger('submit')
    await flush()
    expect(wrapper.find('[data-testid="relay-secret-error"]').text()).toContain('!handle')
    expect(wrapper.find('[data-testid="relay-secrets-save"]').attributes('disabled')).toBeUndefined()
  })
})
