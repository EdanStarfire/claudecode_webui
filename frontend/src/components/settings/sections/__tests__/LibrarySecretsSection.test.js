import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import LibrarySecretsSection from '@/components/settings/sections/LibrarySecretsSection.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

async function mountWithSecrets(secrets) {
  apiMock.get.mockImplementation((url) => {
    if (url === '/api/secrets') return Promise.resolve({ secrets })
    if (url === '/api/system/secrets-backend-status') return Promise.resolve({})
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })

  const rendered = renderWithStores(LibrarySecretsSection)
  await flush()
  return rendered
}

function chipFor(wrapper, name) {
  const row = wrapper.findAll('.secret-row').find(r => r.find('.secret-name').text() === name)
  return row.find('.usage-chip')
}

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('LibrarySecretsSection usage chip', () => {
  it('shows "N in use" when usage.total > 0', async () => {
    const { wrapper } = await mountWithSecrets([
      { name: 'used-secret', type: 'generic', target_hosts: [], usage: {
        sessions: 2, templates: 1, profiles: 0, mcp_servers: 0, oauth2_dependents: [], total: 3
      } },
    ])
    expect(chipFor(wrapper, 'used-secret').text()).toBe('3 in use')
  })

  it('shows "Unused" when usage.total is 0', async () => {
    const { wrapper } = await mountWithSecrets([
      { name: 'unused-secret', type: 'generic', target_hosts: [], usage: {
        sessions: 0, templates: 0, profiles: 0, mcp_servers: 0, oauth2_dependents: [], total: 0
      } },
    ])
    expect(chipFor(wrapper, 'unused-secret').text()).toBe('Unused')
  })

  it('shows "Unused" when usage is missing entirely', async () => {
    const { wrapper } = await mountWithSecrets([
      { name: 'no-usage-secret', type: 'generic', target_hosts: [] },
    ])
    expect(chipFor(wrapper, 'no-usage-secret').text()).toBe('Unused')
  })

  it('title breaks down all four category counts', async () => {
    const { wrapper } = await mountWithSecrets([
      { name: 'breakdown-secret', type: 'generic', target_hosts: [], usage: {
        sessions: 2, templates: 1, profiles: 4, mcp_servers: 3, oauth2_dependents: [], total: 10
      } },
    ])
    const title = chipFor(wrapper, 'breakdown-secret').attributes('title')
    expect(title).toContain('Sessions: 2')
    expect(title).toContain('Templates: 1')
    expect(title).toContain('Profiles: 4')
    expect(title).toContain('MCP servers: 3')
    expect(title).not.toContain('OAuth2 dependency')
  })

  it('title includes the oauth2_dependents line when present', async () => {
    const { wrapper } = await mountWithSecrets([
      { name: 'oauth-sibling-secret', type: 'generic', target_hosts: [], usage: {
        sessions: 0, templates: 0, profiles: 0, mcp_servers: 0,
        oauth2_dependents: ['github-oauth'], total: 1
      } },
    ])
    const title = chipFor(wrapper, 'oauth-sibling-secret').attributes('title')
    expect(title).toContain('OAuth2 dependency: github-oauth')
  })
})
