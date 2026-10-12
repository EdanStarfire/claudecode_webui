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
