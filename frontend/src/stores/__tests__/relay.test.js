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

  it('saveSecrets sends only the selected secret NAMES', async () => {
    apiMock.put.mockResolvedValue({ ...REC, has_user_token: true, user_token_secret: 'slack-user' })
    const store = useRelayStore()
    store.relays = [REC]
    await store.saveSecrets('r1', { userSecret: 'slack-user', appSecret: '' })
    expect(apiMock.put).toHaveBeenCalledWith('/api/relays/r1/secrets', { user_token_secret: 'slack-user' })
    expect(store.relays[0].has_user_token).toBe(true)
    expect(store.saveTokens).toBeUndefined()
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

describe('relay store: policy editor / picker / audit helpers', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach(fn => fn.mockReset())
  })

  it('searchConversations maps params and unwraps structured errors', async () => {
    const store = useRelayStore()
    apiMock.get.mockResolvedValue({ entries: [] })
    await store.searchConversations('r1', { q: 'dev', kinds: ['public', 'person'], cursor: 'o:20' })
    expect(apiMock.get).toHaveBeenCalledWith('/api/relays/r1/conversations',
      { params: { q: 'dev', kinds: 'public,person', limit: null, cursor: 'o:20' }, signal: undefined })
    apiMock.get.mockRejectedValue(Object.assign(new Error('x'), { data: { detail: { code: 'query_required', message: 'Need 2' } } }))
    await expect(store.searchConversations('r1', { q: 'a' })).rejects.toThrow('Need 2')
  })

  it('resolveLabels caches per relay+id, falls back to the ID and retries failures', async () => {
    const store = useRelayStore()
    apiMock.get.mockResolvedValueOnce({ labels: { C0AAAAAAA1: '#a' } })
    expect(await store.resolveLabels('r1', ['C0AAAAAAA1'])).toEqual({ C0AAAAAAA1: '#a' })
    await store.resolveLabels('r1', ['C0AAAAAAA1'])
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    apiMock.get.mockRejectedValueOnce(new Error('down'))
    expect(await store.resolveLabels('r1', ['C0BBBBBBB2'])).toEqual({ C0BBBBBBB2: 'C0BBBBBBB2' })
    apiMock.get.mockResolvedValueOnce({ labels: { C0BBBBBBB2: '#b' } })
    expect(await store.resolveLabels('r1', ['C0BBBBBBB2'])).toEqual({ C0BBBBBBB2: '#b' })
  })

  it('does not cache an unresolved (id-as-label) answer', async () => {
    const store = useRelayStore()
    apiMock.get.mockResolvedValueOnce({ labels: { C0AAAAAAA1: 'C0AAAAAAA1' } })
    await store.resolveLabels('r1', ['C0AAAAAAA1'])
    apiMock.get.mockResolvedValueOnce({ labels: { C0AAAAAAA1: '#now-known' } })
    expect(await store.resolveLabels('r1', ['C0AAAAAAA1'])).toEqual({ C0AAAAAAA1: '#now-known' })
  })

  it('resolveLabels asks in chunks of 50', async () => {
    const store = useRelayStore()
    apiMock.get.mockResolvedValue({ labels: {} })
    await store.resolveLabels('r1', Array.from({ length: 120 }, (_, i) => `C0X${String(i).padStart(6, '0')}`))
    expect(apiMock.get).toHaveBeenCalledTimes(3)
  })

  it('setPolicy / removePolicy use the relay-qualified endpoints', async () => {
    const store = useRelayStore()
    apiMock.put.mockResolvedValue({ ok: 1 })
    apiMock.delete.mockResolvedValue({ ok: 2 })
    expect(await store.setPolicy('s1', 'r1', 'C0AAAAAAA1', 'ask')).toEqual({ ok: 1 })
    expect(apiMock.put).toHaveBeenCalledWith('/api/sessions/s1/relays/r1/policies/C0AAAAAAA1', { policy: 'ask' })
    await store.removePolicy('s1', 'r1', 'C0AAAAAAA1')
    expect(apiMock.delete).toHaveBeenCalledWith('/api/sessions/s1/relays/r1/policies/C0AAAAAAA1')
  })

  it('loadAudit passes filters and returns the records', async () => {
    const store = useRelayStore()
    apiMock.get.mockResolvedValue({ records: [{ ts: 't' }] })
    expect(await store.loadAudit('r1', { sessionId: 's1', channel: 'c', limit: 100, before: 't0' })).toEqual([{ ts: 't' }])
    expect(apiMock.get).toHaveBeenCalledWith('/api/relays/r1/audit',
      { params: { session_id: 's1', channel: 'c', limit: 100, before: 't0', events: null, outcome: null, since: null } })
    await store.loadAudit('r1', { events: 'inbound_*', outcome: 'refused', since: 's' })
    expect(apiMock.get.mock.calls.at(-1)[1].params).toMatchObject({ events: 'inbound_*', outcome: 'refused', since: 's' })
  })

  it('getPolicyModel is cached', async () => {
    const store = useRelayStore()
    apiMock.get.mockResolvedValue({ values: ['a'] })
    await store.getPolicyModel('r1')
    await store.getPolicyModel('r1')
    expect(apiMock.get).toHaveBeenCalledTimes(1)
  })
})
