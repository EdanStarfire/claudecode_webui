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

  it('offers vault secrets by name only; secrets used elsewhere are plain selectable options', async () => {
    const secrets = [
      { name: 'slack-user', type: 'generic', usage: { total: 0 } },
      { name: 'in-use', type: 'generic', usage: { sessions: 2, templates: 1, total: 3 } },
      { name: 'deploy-key', type: 'ssh_key', usage: { total: 0 } },
    ]
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/relays') return Promise.resolve({ relays: [REC] })
      if (url === '/api/secrets') return Promise.resolve({ secrets: secrets.map(s => ({ ...s })) })
      if (url.includes('/manifest')) return Promise.resolve(MANIFEST)
      return Promise.resolve({})
    })
    apiMock.put.mockResolvedValue({ ...REC, has_user_token: true, user_token_secret: 'in-use' })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('[data-testid="relay-secret-pickers"] input').exists()).toBe(false)
    expect(wrapper.find('[data-testid="relay-user-token"]').exists()).toBe(false)
    const select = wrapper.find('[data-testid="relay-pick-user"]')
    const optionEls = select.findAll('option')
    expect(optionEls.map(o => o.text())).toEqual(['None selected', 'in-use', 'slack-user'])
    expect(optionEls.every(o => o.attributes('disabled') === undefined)).toBe(true)
    expect(wrapper.find('[data-testid="relay-create-secret-link"]').attributes('href')).toContain('/settings/secrets')
    await select.setValue('in-use')
    await flush()
    // no sharing noise anywhere
    expect(wrapper.text()).not.toMatch(/also assigned|also exposed|Dedicated secrets/i)
    expect(apiMock.get.mock.calls.some(c => String(c[0]).includes('secret-usage'))).toBe(false)
    await wrapper.find('[data-testid="relay-secret-pickers"]').trigger('submit')
    await flush()
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1/secrets', { user_token_secret: 'in-use' })
    expect(wrapper.find('[data-testid="relay-chip-user"]').text()).toContain('set')
    expect(wrapper.text()).not.toMatch(/also assigned|also exposed/i)
  })

  it('shows the backend validation error and keeps the form usable', async () => {
    mockApi([REC])
    apiMock.put.mockRejectedValue(new Error("user_token_secret: the value of secret 'x' must start with 'xoxp-'"))
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/relays') return Promise.resolve({ relays: [REC] })
      if (url === '/api/secrets') return Promise.resolve({ secrets: [{ name: 'x', type: 'generic', usage: { total: 0 } }] })
      return Promise.resolve(MANIFEST)
    })
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    await wrapper.find('[data-testid="relay-pick-user"]').setValue('x')
    await wrapper.find('[data-testid="relay-secret-pickers"]').trigger('submit')
    await flush()
    expect(wrapper.find('[data-testid="relay-secret-error"]').text()).toContain('xoxp-')
  })

  it('shows a missing-secret state when the referenced secret was deleted', async () => {
    mockApi([{ ...REC, user_token_secret: 'gone', user_secret_missing: true, has_user_token: false }])
    const { wrapper } = renderWithStores(LibraryRelaysSection)
    await flush()
    expect(wrapper.find('[data-testid="relay-chip-user"]').text()).toContain('secret missing')
    expect(wrapper.find('[data-testid="relay-missing-user"]').text()).toContain("'gone'")
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
