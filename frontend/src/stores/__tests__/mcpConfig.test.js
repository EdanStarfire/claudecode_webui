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

describe('mcpConfig store — fetchTools (issue #1799)', () => {
  async function getStore() {
    const { useMcpConfigStore } = await import('@/stores/mcpConfig')
    return useMcpConfigStore()
  }

  it('caches the connected result and clears checkingToolsIds on success', async () => {
    apiMock.get.mockResolvedValue({
      status: 'connected',
      tools: [{ name: 'ping', description: 'ping tool' }],
      error: null,
    })
    const store = await getStore()

    const promise = store.fetchTools('cfg-1')
    expect(store.checkingToolsIds.has('cfg-1')).toBe(true)
    const result = await promise

    expect(apiMock.get).toHaveBeenCalledWith('/api/mcp-configs/cfg-1/tools')
    expect(result.status).toBe('connected')
    expect(store.toolsByConfigId.get('cfg-1')).toEqual(result)
    expect(store.checkingToolsIds.has('cfg-1')).toBe(false)
  })

  it('tracks two concurrent checks independently (issue #1799 regression)', async () => {
    let resolveA
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/mcp-configs/cfg-a/tools') {
        return new Promise((resolve) => { resolveA = resolve })
      }
      return Promise.resolve({ status: 'connected', tools: [], error: null })
    })
    const store = await getStore()

    const promiseA = store.fetchTools('cfg-a')
    expect(store.checkingToolsIds.has('cfg-a')).toBe(true)

    await store.fetchTools('cfg-b')
    expect(store.checkingToolsIds.has('cfg-a')).toBe(true)
    expect(store.checkingToolsIds.has('cfg-b')).toBe(false)

    resolveA({ status: 'connected', tools: [], error: null })
    await promiseA
    expect(store.checkingToolsIds.has('cfg-a')).toBe(false)
  })

  it('caches a failed fallback (not a throw) when the request errors', async () => {
    apiMock.get.mockRejectedValue({ data: { detail: 'connection refused' }, message: 'boom' })
    const store = await getStore()

    const result = await store.fetchTools('cfg-2')

    expect(result).toEqual({ status: 'failed', tools: [], error: 'connection refused' })
    expect(store.toolsByConfigId.get('cfg-2')).toEqual(result)
    expect(store.checkingToolsIds.has('cfg-2')).toBe(false)
  })

  it('drops a cached tools result when the config is updated (issue #1799 stale-cache fix)', async () => {
    apiMock.get.mockResolvedValue({ status: 'connected', tools: [{ name: 'ping' }], error: null })
    apiMock.put.mockResolvedValue({ id: 'cfg-3', name: 'renamed' })
    const store = await getStore()

    await store.fetchTools('cfg-3')
    expect(store.toolsByConfigId.get('cfg-3')).toBeDefined()

    await store.updateConfig('cfg-3', { name: 'renamed' })

    expect(store.toolsByConfigId.has('cfg-3')).toBe(false)
  })

  it('drops a cached tools result when the config is deleted (issue #1799 stale-cache fix)', async () => {
    apiMock.get.mockResolvedValue({ status: 'connected', tools: [{ name: 'ping' }], error: null })
    apiMock.delete.mockResolvedValue({ deleted: true })
    const store = await getStore()

    await store.fetchTools('cfg-4')
    expect(store.toolsByConfigId.get('cfg-4')).toBeDefined()

    await store.deleteConfig('cfg-4')

    expect(store.toolsByConfigId.has('cfg-4')).toBe(false)
  })
})
