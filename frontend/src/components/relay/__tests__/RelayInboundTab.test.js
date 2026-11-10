import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderWithStores } from '@/test-utils/render'
import RelayInboundTab from '@/components/relay/RelayInboundTab.vue'
import { health, monitor } from './monitorFixtures'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const ROUTES = [{ path: '/settings/relays', component: { template: '<div/>' } }, { path: '/', component: { template: '<div/>' } }]
let healthPayload
const calls = (suffix) => apiMock.get.mock.calls.filter(c => c[0].endsWith(suffix)).length

function mountTab(props = {}) {
  return renderWithStores(RelayInboundTab, { props: { relayId: 'r1', ...props }, routes: ROUTES })
}

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  healthPayload = health()
  apiMock.get.mockImplementation((url) => {
    if (url.endsWith('/inbound/health')) return Promise.resolve(healthPayload)
    if (url.endsWith('/monitors')) return Promise.resolve({ monitors: [monitor()], total: 1 })
    if (url.endsWith('/audit')) return Promise.resolve({ records: [] })
    return Promise.resolve({})
  })
})
afterEach(() => vi.useRealTimers())

describe('RelayInboundTab', () => {
  it('shows health, the monitors table and the activity feed', async () => {
    const { wrapper } = mountTab()
    await flushPromises()
    expect(wrapper.find('[data-testid="inbound-health"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="monitor-row"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="activity-feed"]').exists()).toBe(true)
  })

  it('emits attention only for attention-level items', async () => {
    healthPayload = health({ attention: [{ code: 'monitors_paused', severity: 'info', count: 1, audit: { events: [] } }] })
    let r = mountTab()
    await flushPromises()
    expect(r.wrapper.emitted('attention')[0]).toEqual([false])
    healthPayload = health({ attention: [{ code: 'auth_failed', severity: 'attention', count: 1, audit: { events: [] } }] })
    r = mountTab()
    await flushPromises()
    expect(r.wrapper.emitted('attention')[0]).toEqual([true])
  })

  it('reads health once while hidden, loads the rest and polls only when active', async () => {
    vi.useFakeTimers()
    const { wrapper } = mountTab({ active: false, pollMs: 1000 })
    await vi.advanceTimersByTimeAsync(5000)
    expect(calls('/inbound/health')).toBe(1)
    expect(calls('/monitors')).toBe(0)
    expect(calls('/audit')).toBe(0)
    await wrapper.setProps({ active: true })
    await vi.advanceTimersByTimeAsync(2100)
    expect(calls('/inbound/health')).toBeGreaterThanOrEqual(3)
    expect(calls('/monitors')).toBeGreaterThanOrEqual(2)
    expect(calls('/audit')).toBe(1) // the feed never polls
    await wrapper.setProps({ active: false })
    const n = calls('/inbound/health')
    await vi.advanceTimersByTimeAsync(5000)
    expect(calls('/inbound/health')).toBe(n)
  })

  it('does not poll while the document is hidden and stops on unmount', async () => {
    vi.useFakeTimers()
    const { wrapper } = mountTab({ pollMs: 1000 })
    await vi.advanceTimersByTimeAsync(0)
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const n = calls('/inbound/health')
    await vi.advanceTimersByTimeAsync(5000)
    expect(calls('/inbound/health')).toBe(n)
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(calls('/inbound/health')).toBe(n)
  })

  it('shows a health error as text and reloads health after a monitor action', async () => {
    apiMock.patch.mockResolvedValue({ ...monitor(), paused: true, effective_state: 'paused' })
    const { wrapper } = mountTab()
    await flushPromises()
    const before = calls('/inbound/health')
    await wrapper.find('[data-testid="monitor-toggle"]').trigger('click')
    await flushPromises()
    expect(calls('/inbound/health')).toBe(before + 1)
    apiMock.get.mockRejectedValue(new Error('boom'))
    await wrapper.vm.$.exposed.reload()
    await flushPromises()
    expect(wrapper.find('[data-testid="inbound-health-error"]').text()).toBe('boom')
  })
})
