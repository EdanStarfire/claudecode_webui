import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRelayStore } from '@/stores/relay'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('relay store: inbound tab calls', () => {
  it('loadInboundHealth and loadMonitors hit the relay-qualified routes', async () => {
    apiMock.get.mockResolvedValue({ monitors: [], total: 0 })
    const store = useRelayStore()
    await store.loadInboundHealth('r 1')
    expect(apiMock.get.mock.calls[0][0]).toBe('/api/relays/r%201/inbound/health')
    await store.loadMonitors('r1', { sessionId: 's1', status: 'paused', limit: 100, offset: 100 })
    expect(apiMock.get.mock.calls[1]).toEqual(['/api/relays/r1/monitors', { params: { session_id: 's1', status: 'paused', kind: null, mode: null, limit: 100, offset: 100 }, signal: undefined }])
  })

  it('runMonitor posts to the relay or session route and unwraps structured errors', async () => {
    const store = useRelayStore()
    apiMock.post.mockResolvedValue({ started: true, monitor: { id: 'm1' } })
    expect(await store.runMonitor('r1', 'm1')).toEqual({ started: true, monitor: { id: 'm1' } })
    expect(apiMock.post.mock.calls[0][0]).toBe('/api/relays/r1/monitors/m1/run')
    await store.runMonitor('r1', 'm1', 's 1')
    expect(apiMock.post.mock.calls[1][0]).toBe('/api/sessions/s%201/relays/r1/monitors/m1/run')
    apiMock.post.mockRejectedValue(Object.assign(new Error('x'), { status: 429, data: { detail: { code: 'run_too_soon', message: 'wait', retry_after_seconds: 41 } } }))
    await expect(store.runMonitor('r1', 'm1')).rejects.toMatchObject({ status: 429, code: 'run_too_soon', retryAfter: 41, message: 'wait' })
    apiMock.post.mockRejectedValue(Object.assign(new Error('Monitor not found'), { status: 404, data: { detail: 'Monitor not found' } }))
    await expect(store.runMonitor('r1', 'm1')).rejects.toMatchObject({ status: 404, code: '' })
  })

  it('a 409 (inbound not running) reads as an empty list; other errors propagate', async () => {
    const store = useRelayStore()
    apiMock.get.mockRejectedValueOnce(Object.assign(new Error('x'), { status: 409 }))
    expect(await store.loadMonitors('r1')).toMatchObject({ monitors: [], total: 0, notRunning: true })
    apiMock.get.mockRejectedValueOnce(Object.assign(new Error('boom'), { status: 500 }))
    await expect(store.loadMonitors('r1')).rejects.toThrow('boom')
  })

  it('pause / delete send PATCH / DELETE and surface the status on errors', async () => {
    const store = useRelayStore()
    apiMock.patch.mockResolvedValue({ id: 'm1', paused: true })
    expect(await store.setMonitorPaused('r1', 'm1', true)).toEqual({ id: 'm1', paused: true })
    expect(apiMock.patch).toHaveBeenCalledWith('/api/relays/r1/monitors/m1', { paused: true })
    apiMock.delete.mockRejectedValue({ message: 'x', status: 404, data: { detail: 'Monitor not found' } })
    await expect(store.deleteMonitor('r1', 'm1')).rejects.toMatchObject({ message: 'x', status: 404 })
  })

  it('activity rows never keep message text previews', async () => {
    apiMock.get.mockResolvedValue({ records: [{ event: 'inbound_instruction', text_preview: 'secret', text_sha256: 'abc', outcome: 'delivered' }] })
    const rows = await useRelayStore().loadInboundActivity('r1')
    expect(rows).toEqual([{ event: 'inbound_instruction', outcome: 'delivered' }])
    expect(apiMock.get.mock.calls[0][1].params).toMatchObject({ events: 'inbound_*,monitor_*', limit: 50 })
  })
})
