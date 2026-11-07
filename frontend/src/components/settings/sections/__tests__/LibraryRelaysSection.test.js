import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import LibraryRelaysSection from '@/components/settings/sections/LibraryRelaysSection.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const flush = () => new Promise(r => setTimeout(r, 0))
const REC = {
  relay_id: 'r1', name: 'Slack', adapter_type: 'slack', workspace_name: null, owner_user_id: null,
  has_user_token: false, has_app_token: false,
}
const MANIFEST = {
  manifest: {}, manifest_json: '{}', steps: [],
  scopes: { required: [], optional: [], inbound: [] },
}

function mockApi(relays) {
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/relays') return Promise.resolve({ relays })
    if (url.includes('/manifest')) return Promise.resolve(MANIFEST)
    if (url === '/api/relays/r1') return Promise.resolve(relays[0])
    return Promise.resolve({})
  })
}

function setLocation(protocol, hostname) {
  Object.defineProperty(window, 'location', {
    value: { protocol, hostname, origin: `${protocol}//${hostname}` }, configurable: true,
  })
}

describe('LibraryRelaysSection', () => {
  const realLocation = window.location
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
    setLocation('http:', 'localhost')
  })
  afterEach(() => Object.defineProperty(window, 'location', { value: realLocation, configurable: true }))

  it('shows the create form when no relay exists and creates one', async () => {
    mockApi([])
    apiMock.post.mockResolvedValue(REC)
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('[data-testid="relay-empty"]').exists()).toBe(true)
    await wrapper.find('[data-testid="relay-create-name"]').setValue('My Slack')
    await wrapper.find('form.create-form').trigger('submit')
    await flush()
    expect(apiMock.post).toHaveBeenCalledWith('/api/relays', { name: 'My Slack', adapter_type: 'slack' })
    expect(wrapper.find('[data-testid="relay-empty"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-detail"]').exists()).toBe(true)
  })

  it('hides create once a relay exists and shows boolean token chips only', async () => {
    mockApi([{ ...REC, has_user_token: true }])
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('[data-testid="relay-create"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-chip-user"]').text()).toContain('set')
    expect(wrapper.find('[data-testid="relay-chip-app"]').text()).toContain('missing')
  })

  it('shows a 409 from a racing create inline', async () => {
    mockApi([])
    apiMock.post.mockRejectedValue(new Error('A relay already exists'))
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    await wrapper.find('form.create-form').trigger('submit')
    await flush()
    expect(wrapper.find('[data-testid="relay-create-error"]').text()).toBe('A relay already exists')
  })

  it('saves tokens, clears the inputs and never renders the value', async () => {
    mockApi([REC])
    apiMock.put.mockResolvedValue({ ...REC, has_user_token: true })
    const { wrapper } = renderWithStores(LibraryRelaysSection, { attachTo: document.body })
    await flush()
    const input = wrapper.find('[data-testid="relay-user-token"]')
    await input.setValue('xoxp-supersecret')
    await wrapper.find('.token-form').trigger('submit')
    await flush()
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1/tokens', { user_token: 'xoxp-supersecret' })
    expect(input.element.value).toBe('')
    expect(document.body.innerHTML).not.toContain('xoxp-supersecret')
    expect(wrapper.find('[data-testid="relay-chip-user"]').text()).toContain('set')
    wrapper.unmount()
  })

  it('shows the backend token error and still clears the input', async () => {
    mockApi([REC])
    apiMock.put.mockRejectedValue(new Error('Slack rejected the user token (invalid_auth)'))
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    const input = wrapper.find('[data-testid="relay-user-token"]')
    await input.setValue('xoxp-bad')
    await wrapper.find('.token-form').trigger('submit')
    await flush()
    expect(wrapper.find('[data-testid="relay-token-error"]').text()).toContain('invalid_auth')
    expect(input.element.value).toBe('')
  })

  it('Test connection renders workspace and user as text', async () => {
    mockApi([{ ...REC, has_user_token: true }])
    apiMock.post.mockResolvedValue({ ok: true, workspace_name: 'Acme <i>Inc</i>', user: 'edan', user_id: 'U1' })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    await wrapper.find('[data-testid="relay-test"]').trigger('click')
    await flush()
    const result = wrapper.find('[data-testid="relay-test-result"]')
    expect(result.text()).toContain('Acme <i>Inc</i>')
    expect(result.find('i').exists()).toBe(false)
  })

  it('shows a sanitized error code when the test fails', async () => {
    mockApi([{ ...REC, has_user_token: true }])
    apiMock.post.mockResolvedValue({ ok: false, error: 'invalid_auth' })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    await wrapper.find('[data-testid="relay-test"]').trigger('click')
    await flush()
    expect(wrapper.find('[data-testid="relay-test-result"]').text()).toContain('invalid_auth')
  })

  it('deletes after confirmation', async () => {
    mockApi([REC])
    apiMock.delete.mockResolvedValue({ deleted: true })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    await wrapper.find('[data-testid="relay-delete"]').trigger('click')
    expect(apiMock.delete).not.toHaveBeenCalled()
    await wrapper.find('[data-testid="relay-delete-confirm"]').trigger('click')
    await flush()
    expect(apiMock.delete).toHaveBeenCalledWith('/api/relays/r1')
    expect(wrapper.find('[data-testid="relay-empty"]').exists()).toBe(true)
  })

  it('hides OAuth on http non-local hosts and explains why', async () => {
    setLocation('http:', '192.168.1.5')
    mockApi([REC])
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('[data-testid="relay-oauth"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-oauth-unavailable"]').text()).toContain('https')
  })

  it.each([['https:', 'ui.example.com'], ['http:', 'localhost'], ['http:', '127.0.0.1']])(
    'shows OAuth on %s//%s', async (protocol, host) => {
      setLocation(protocol, host)
      mockApi([REC])
      const { wrapper } = renderWithStores(LibraryRelaysSection)
      await flush()
      expect(wrapper.find('[data-testid="relay-oauth"]').exists()).toBe(true)
    })
})
