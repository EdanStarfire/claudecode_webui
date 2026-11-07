import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRelayStore } from '@/stores/relay'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const REC = { relay_id: 'r1', name: 'Slack', has_user_token: false, has_app_token: false }

describe('relay store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
  })

  it('loads relays and exposes the first as the active relay', async () => {
    apiMock.get.mockResolvedValue({ relays: [REC] })
    const store = useRelayStore()
    await store.loadRelays()
    expect(store.relay.relay_id).toBe('r1')
    expect(store.loaded).toBe(true)
  })

  it('records a load error message', async () => {
    apiMock.get.mockRejectedValue(new Error('boom'))
    const store = useRelayStore()
    await store.loadRelays()
    expect(store.error).toBe('boom')
  })

  it('create rethrows the backend message (409) without adding a relay', async () => {
    apiMock.post.mockRejectedValue(new Error('A relay already exists'))
    const store = useRelayStore()
    await expect(store.createRelay('x')).rejects.toThrow('A relay already exists')
    expect(store.relays).toEqual([])
  })

  it('saveTokens sends only provided tokens and never stores them', async () => {
    apiMock.put.mockResolvedValue({ ...REC, has_user_token: true })
    const store = useRelayStore()
    store.relays = [REC]
    await store.saveTokens('r1', { userToken: 'xoxp-secret-value', appToken: '' })
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1/tokens', { user_token: 'xoxp-secret-value' })
    expect(store.relays[0].has_user_token).toBe(true)
    expect(JSON.stringify(store.$state)).not.toContain('xoxp-secret-value')
  })

  it('testRelay refreshes the record after the test', async () => {
    apiMock.post.mockResolvedValue({ ok: true, workspace_name: 'W' })
    apiMock.get.mockResolvedValue({ ...REC, workspace_name: 'W' })
    const store = useRelayStore()
    store.relays = [REC]
    const result = await store.testRelay('r1')
    expect(result.ok).toBe(true)
    expect(store.relays[0].workspace_name).toBe('W')
  })

  it('deleteRelay removes the record; manifest and handle use the documented routes', async () => {
    apiMock.delete.mockResolvedValue({ deleted: true })
    apiMock.get.mockResolvedValue({})
    apiMock.put.mockResolvedValue({ relay_handle: 'h' })
    const store = useRelayStore()
    store.relays = [REC]
    await store.deleteRelay('r1')
    expect(store.relays).toEqual([])
    await store.loadManifest('r1', false)
    expect(apiMock.get).toHaveBeenCalledWith('/api/relays/r1/manifest?include_inbound=false')
    await store.setSessionHandle('s1', 'h')
    expect(apiMock.put).toHaveBeenCalledWith('/api/sessions/s1/relay/handle', { handle: 'h' })
  })
})
