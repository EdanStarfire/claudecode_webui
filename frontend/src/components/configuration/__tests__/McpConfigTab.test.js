import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import McpConfigTab from '@/components/configuration/McpConfigTab.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ configs: [], secrets: [] })
  apiMock.post.mockResolvedValue({ id: 'cfg-1', name: 'test', type: 'stdio', env: {} })
  apiMock.put.mockResolvedValue({ id: 'cfg-1', name: 'test', type: 'stdio', env: {} })
})

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

async function openCreateForm() {
  const rendered = renderWithStores(McpConfigTab)
  await flush()
  await fireEvent.click(screen.getByRole('button', { name: '+ Add MCP Server' }))
  return rendered
}

describe('McpConfigTab — discard warning on unsaved env var/header (issue #2072)', () => {
  it('warns naming the key when saving with a pending env var', async () => {
    await openCreateForm()
    await fireEvent.update(screen.getByPlaceholderText('KEY'), 'API_KEY')
    await fireEvent.update(screen.getByPlaceholderText('value'), 'secret-val')

    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    expect(screen.getByText('API_KEY')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save Anyway' })).toBeTruthy()
    expect(apiMock.post).not.toHaveBeenCalled()
  })

  it('"Save Anyway" saves without the never-committed var and clears the warning', async () => {
    await openCreateForm()
    await fireEvent.update(screen.getByPlaceholderText('KEY'), 'API_KEY')
    await fireEvent.update(screen.getByPlaceholderText('value'), 'secret-val')
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await fireEvent.click(screen.getByRole('button', { name: 'Save Anyway' }))
    await flush()

    expect(apiMock.post).toHaveBeenCalledTimes(1)
    const [, payload] = apiMock.post.mock.calls[0]
    expect(payload.env).toEqual({})
    expect(screen.queryByRole('button', { name: 'Save Anyway' })).toBeFalsy()
  })

  it('"Go Back" dismisses the warning and preserves staging inputs without saving', async () => {
    await openCreateForm()
    await fireEvent.update(screen.getByPlaceholderText('KEY'), 'API_KEY')
    await fireEvent.update(screen.getByPlaceholderText('value'), 'secret-val')
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await fireEvent.click(screen.getByRole('button', { name: 'Go Back' }))

    expect(screen.queryByRole('button', { name: 'Save Anyway' })).toBeFalsy()
    expect(screen.getByPlaceholderText('KEY').value).toBe('API_KEY')
    expect(screen.getByPlaceholderText('value').value).toBe('secret-val')
    expect(apiMock.post).not.toHaveBeenCalled()
  })

  it('saves immediately with no warning when staging inputs are empty (regression)', async () => {
    await openCreateForm()
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await flush()

    expect(apiMock.post).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Save Anyway' })).toBeFalsy()
  })

  it('treats a whitespace-only staging key as nothing pending', async () => {
    await openCreateForm()
    await fireEvent.update(screen.getByPlaceholderText('KEY'), '   ')
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await flush()

    expect(apiMock.post).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Save Anyway' })).toBeFalsy()
  })

  it('still warns when a key is typed but the value is left empty', async () => {
    await openCreateForm()
    await fireEvent.update(screen.getByPlaceholderText('KEY'), 'API_KEY')
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    expect(screen.getByRole('button', { name: 'Save Anyway' })).toBeTruthy()
    expect(apiMock.post).not.toHaveBeenCalled()
  })

  it('warns naming the header key for an SSE/HTTP server', async () => {
    await openCreateForm()
    await fireEvent.click(screen.getByRole('button', { name: 'SSE' }))
    await fireEvent.update(screen.getByPlaceholderText('Header'), 'Authorization')
    await fireEvent.update(screen.getByPlaceholderText('value'), 'Bearer xyz')

    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    expect(screen.getByText('Authorization')).toBeTruthy()
    expect(apiMock.post).not.toHaveBeenCalled()
  })

  it('auto-clears a stale warning when the pending var is committed via "+" instead of Go Back', async () => {
    await openCreateForm()
    await fireEvent.update(screen.getByPlaceholderText('KEY'), 'API_KEY')
    await fireEvent.update(screen.getByPlaceholderText('value'), 'secret-val')
    await fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(screen.getByRole('button', { name: 'Save Anyway' })).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: '+' }))

    expect(screen.queryByRole('button', { name: 'Save Anyway' })).toBeFalsy()
  })
})

describe('McpConfigTab — Connect/Reconnect error + discovery surfacing (issue #2247)', () => {
  function _oauthConfig(overrides = {}) {
    return {
      id: 'cfg-oauth-1',
      name: 'oauth-server',
      type: 'http',
      url: 'https://example.com/mcp',
      oauth_enabled: true,
      headers: {},
      ...overrides,
    }
  }

  function _mockConfigsAndStatus(config) {
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/mcp-configs') return Promise.resolve({ configs: [config] })
      if (url.includes('/oauth/status')) return Promise.resolve({ status: 'unauthenticated' })
      return Promise.resolve({ secrets: [] })
    })
  }

  it('renders the initiate-500 detail inline on the server entry (AC1/AC4)', async () => {
    const config = _oauthConfig()
    _mockConfigsAndStatus(config)
    const detail =
      "OAuth Dynamic Client Registration failed for MCP server 'oauth-server' and no " +
      "oauth_client_id is configured. Set 'oauth_client_id' in the MCP server config " +
      'to use a pre-registered app.'
    apiMock.post.mockRejectedValueOnce({ data: { detail }, message: 'Request failed' })

    renderWithStores(McpConfigTab)
    await flush()

    await fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await flush()

    expect(screen.getByText(detail)).toBeTruthy()
  })

  it('clears a stale Connect error on a retry that succeeds', async () => {
    const config = _oauthConfig()
    _mockConfigsAndStatus(config)
    apiMock.post
      .mockRejectedValueOnce({ data: { detail: 'first attempt failed' }, message: 'fail' })
      .mockResolvedValueOnce({ auth_url: 'https://auth.example.com/authorize', discovery: {} })
    vi.spyOn(window, 'open').mockImplementation(() => null)

    renderWithStores(McpConfigTab)
    await flush()

    await fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await flush()
    expect(screen.getByText('first attempt failed')).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await flush()

    expect(screen.queryByText('first attempt failed')).toBeFalsy()
  })

  it('renders a discovery summary line when the response carries a notable error key (AC1)', async () => {
    const config = _oauthConfig()
    _mockConfigsAndStatus(config)
    apiMock.post.mockResolvedValueOnce({
      auth_url: 'https://auth.example.com/authorize',
      discovery: {
        mechanism: 'dcr',
        dcr_error: { type: 'Exception', message: 'registration failed, falling back' },
      },
    })
    vi.spyOn(window, 'open').mockImplementation(() => null)

    renderWithStores(McpConfigTab)
    await flush()

    await fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await flush()

    expect(screen.getByText(/dcr_error: registration failed, falling back/)).toBeTruthy()
  })

  it('renders nothing extra for a clean successful connect (no error keys in discovery)', async () => {
    const config = _oauthConfig()
    _mockConfigsAndStatus(config)
    apiMock.post.mockResolvedValueOnce({
      auth_url: 'https://auth.example.com/authorize',
      discovery: { mechanism: 'dcr' },
    })
    vi.spyOn(window, 'open').mockImplementation(() => null)

    renderWithStores(McpConfigTab)
    await flush()

    await fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await flush()

    expect(screen.queryByText(/mechanism:/)).toBeFalsy()
  })

  it('still shows the needs-auth hint when the tools response carries the new expires_at field (AC2 regression)', async () => {
    const config = { id: 'cfg-shared-1', name: 'shared-server', type: 'http', url: 'https://example.com/mcp', shared_connection: true, oauth_enabled: false, headers: {} }
    _mockConfigsAndStatus(config)
    apiMock.get.mockImplementation((url) => {
      if (url === '/api/mcp-configs') return Promise.resolve({ configs: [config] })
      if (url.includes('/tools')) {
        return Promise.resolve({ status: 'needs-auth', tools: [], error: null, expires_at: 12345 })
      }
      return Promise.resolve({ secrets: [] })
    })

    renderWithStores(McpConfigTab)
    await flush()

    await fireEvent.click(screen.getByRole('button', { name: 'Show tools' }))
    await flush()

    expect(screen.getByText('No stored OAuth token — connect above, then check again.')).toBeTruthy()
  })
})
