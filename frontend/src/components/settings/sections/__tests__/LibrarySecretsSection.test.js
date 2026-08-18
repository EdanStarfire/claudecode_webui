import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import LibrarySecretsSection from '@/components/settings/sections/LibrarySecretsSection.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

function usageChipFor(wrapper, secretName) {
  const row = wrapper.findAll('.secret-row').find(
    r => r.find('.secret-name').text() === secretName
  )
  return row?.find('.usage-chip')
}

describe('LibrarySecretsSection usage chip', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/secrets') {
        return Promise.resolve({
          secrets: [
            {
              name: 'unused-secret',
              type: 'generic',
              target_hosts: ['example.com'],
              usage: { sessions: 0, templates: 0, profiles: 0, mcp_servers: 0, oauth2_dependents: [], total: 0 },
            },
            {
              name: 'used-secret',
              type: 'generic',
              target_hosts: ['example.com'],
              usage: { sessions: 2, templates: 1, profiles: 0, mcp_servers: 0, oauth2_dependents: [], total: 3 },
            },
            {
              name: 'oauth-dep-secret',
              type: 'generic',
              target_hosts: ['example.com'],
              usage: { sessions: 0, templates: 0, profiles: 0, mcp_servers: 0, oauth2_dependents: ['github-oauth'], total: 1 },
            },
            {
              name: 'no-usage-field-secret',
              type: 'generic',
              target_hosts: ['example.com'],
            },
          ],
        })
      }
      return Promise.resolve({})
    })
  })

  it('shows "Unused" with the dimmer chip class when total is 0', async () => {
    const { wrapper } = renderWithStores(LibrarySecretsSection)
    await flush()

    const chip = usageChipFor(wrapper, 'unused-secret')
    expect(chip.text()).toBe('Unused')
    expect(chip.classes()).toContain('usage-chip--unused')
  })

  it('shows "N in use" and a title breakdown when total > 0', async () => {
    const { wrapper } = renderWithStores(LibrarySecretsSection)
    await flush()

    const chip = usageChipFor(wrapper, 'used-secret')
    expect(chip.text()).toBe('3 in use')
    expect(chip.classes()).not.toContain('usage-chip--unused')
    expect(chip.attributes('title')).toBe('Sessions: 2 · Templates: 1 · Profiles: 0 · MCP servers: 0')
  })

  it('includes the OAuth2 dependency line in the title when present', async () => {
    const { wrapper } = renderWithStores(LibrarySecretsSection)
    await flush()

    const chip = usageChipFor(wrapper, 'oauth-dep-secret')
    expect(chip.text()).toBe('1 in use')
    expect(chip.attributes('title')).toContain('OAuth2 dependency: github-oauth')
  })

  it('treats a missing usage field as Unused', async () => {
    const { wrapper } = renderWithStores(LibrarySecretsSection)
    await flush()

    const chip = usageChipFor(wrapper, 'no-usage-field-secret')
    expect(chip.text()).toBe('Unused')
    expect(chip.classes()).toContain('usage-chip--unused')
  })
})
