import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
}))

vi.mock('@/utils/api', () => ({
  api: apiMock,
  getAuthToken: vi.fn(() => null),
}))

function stubLocation({ protocol = 'https:', hostname = 'example.com', port = '' } = {}) {
  const host = port ? `${hostname}:${port}` : hostname
  Object.defineProperty(window, 'location', {
    value: { protocol, hostname, port, host, origin: `${protocol}//${host}` },
    writable: true,
    configurable: true,
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  stubLocation()
})

describe('mcpConfig store — buildRedirectUri (issue #1789)', () => {
  async function getStore() {
    const { useMcpConfigStore } = await import('@/stores/mcpConfig')
    return useMcpConfigStore()
  }

  it('defaults to origin + /oauth/callback when neither custom field is set', async () => {
    const store = await getStore()
    expect(store.buildRedirectUri({})).toBe('https://example.com/oauth/callback')
  })

  it('matches window.location.origin behavior when a non-default port is already current', async () => {
    stubLocation({ hostname: 'example.com', port: '5789' })
    const store = await getStore()
    expect(store.buildRedirectUri({})).toBe('https://example.com:5789/oauth/callback')
  })

  it('uses the custom path only, keeping the current host', async () => {
    const store = await getStore()
    const uri = store.buildRedirectUri({ oauth_custom_callback_path: '/callback' })
    expect(uri).toBe('https://example.com/callback')
  })

  it('uses the custom port only, defaulting the path to /oauth/callback', async () => {
    const store = await getStore()
    const uri = store.buildRedirectUri({ oauth_custom_callback_port: 8765 })
    expect(uri).toBe('https://example.com:8765/oauth/callback')
  })

  it('uses both custom path and custom port together', async () => {
    const store = await getStore()
    const uri = store.buildRedirectUri({
      oauth_custom_callback_path: '/callback',
      oauth_custom_callback_port: 8765,
    })
    expect(uri).toBe('https://example.com:8765/callback')
  })

  it('ignores the browser current port when a custom port is set (hostname-based, not origin-based)', async () => {
    stubLocation({ hostname: 'example.com', port: '5789' })
    const store = await getStore()
    const uri = store.buildRedirectUri({ oauth_custom_callback_port: 8765 })
    expect(uri).toBe('https://example.com:8765/oauth/callback')
  })

  it('initiateOAuth posts a redirect_uri built from the stored config', async () => {
    apiMock.post.mockResolvedValue({ auth_url: 'https://provider.example/authorize' })
    const store = await getStore()
    store.configs.set('cfg-1', {
      id: 'cfg-1',
      oauth_custom_callback_path: '/callback',
      oauth_custom_callback_port: 8765,
    })

    await store.initiateOAuth('cfg-1')

    expect(apiMock.post).toHaveBeenCalledWith('/api/mcp-configs/cfg-1/oauth/initiate', {
      redirect_uri: 'https://example.com:8765/callback',
    })
  })
})
