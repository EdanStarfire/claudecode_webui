import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderWithStores } from '@/test-utils/render'
import RelayMonitorList from '@/components/relay/RelayMonitorList.vue'
import { monitor } from './monitorFixtures'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const A = monitor({ id: 'a', channel_label: '#a' })
const B = monitor({ id: 'b', channel_label: '#b', session_id: 's2', session_name: 'Other' })

function mountList(props = {}) {
  return renderWithStores(RelayMonitorList, { props: { relayId: 'r1', ...props } })
}
const rowsOf = (w) => w.findAll('[data-testid="monitor-row"]').map(r => r.attributes('data-monitor-id'))
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ monitors: [A, B], total: 2 })
})
afterEach(() => vi.useRealTimers())

describe('RelayMonitorList loading and filters', () => {
  it('lists relay-wide monitors and says "Applies immediately"', async () => {
    const { wrapper } = mountList()
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['a', 'b'])
    expect(apiMock.get.mock.calls[0][0]).toBe('/api/relays/r1/monitors')
    expect(wrapper.find('[data-testid="monitor-foot"]').text()).toContain('Applies immediately')
  })

  it('state chips, session and kind selects reload with the filter', async () => {
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.find('[data-testid="monitor-filter-suspended"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.status).toBe('suspended')
    await wrapper.find('[data-testid="monitor-filter-session"]').setValue('s2')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.session_id).toBe('s2')
    await wrapper.find('[data-testid="monitor-filter-kind"]').setValue('monitor_dms')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.kind).toBe('monitor_dms')
    expect(wrapper.find('[data-testid="monitor-filter-suspended"]').attributes('aria-pressed')).toBe('true')
  })

  it('empty states, session lock and a 409 (inbound not running)', async () => {
    apiMock.get.mockResolvedValue({ monitors: [], total: 0 })
    let r = mountList()
    await flushPromises()
    expect(r.wrapper.find('[data-testid="monitor-empty"]').text()).toContain('No monitors yet')
    await r.wrapper.find('[data-testid="monitor-filter-paused"]').trigger('click')
    await flushPromises()
    expect(r.wrapper.find('[data-testid="monitor-empty"]').text()).toBe('No monitors match.')
    apiMock.get.mockRejectedValue(Object.assign(new Error('x'), { status: 409 }))
    r = mountList({ sessionId: 's1' })
    await flushPromises()
    expect(r.wrapper.find('[data-testid="monitor-not-running"]').exists()).toBe(true)
    expect(r.wrapper.find('[data-testid="monitor-filter-session"]').exists()).toBe(false)
    expect(apiMock.get.mock.calls.at(-1)[0]).toBe('/api/sessions/s1/relays/r1/monitors') // session-qualified (3d-ii)
    expect(apiMock.get.mock.calls.at(-1)[1].params.session_id).toBeNull()
  })

  it('shows an error for a failed load', async () => {
    apiMock.get.mockRejectedValue(new Error('server down'))
    const { wrapper } = mountList()
    await flushPromises()
    expect(wrapper.find('[data-testid="monitor-load-error"]').text()).toBe('server down')
  })

  it('pages with Load more by offset', async () => {
    apiMock.get.mockResolvedValueOnce({ monitors: [A], total: 2 })
    const { wrapper } = mountList()
    await flushPromises()
    expect(wrapper.find('[data-testid="monitor-more"]').text()).toContain('1')
    apiMock.get.mockResolvedValueOnce({ monitors: [B], total: 2 })
    await wrapper.find('[data-testid="monitor-more"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.offset).toBe(1)
    expect(rowsOf(wrapper)).toEqual(['a', 'b'])
    expect(wrapper.find('[data-testid="monitor-more"]').exists()).toBe(false)
  })
})

describe('RelayMonitorList polling', () => {
  it('polls only while active and not hidden', async () => {
    vi.useFakeTimers()
    const { wrapper } = mountList({ active: false, pollMs: 1000 })
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiMock.get).not.toHaveBeenCalled()
    await wrapper.setProps({ active: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(apiMock.get).toHaveBeenCalledTimes(3)
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    await vi.advanceTimersByTimeAsync(3000)
    expect(apiMock.get).toHaveBeenCalledTimes(3)
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
    await wrapper.setProps({ active: false })
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiMock.get).toHaveBeenCalledTimes(3)
  })

  it('stops polling when unmounted', async () => {
    vi.useFakeTimers()
    const { wrapper } = mountList({ pollMs: 1000 })
    await vi.advanceTimersByTimeAsync(0)
    wrapper.unmount()
    const calls = apiMock.get.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiMock.get).toHaveBeenCalledTimes(calls)
  })
})

describe('RelayMonitorList actions (immediate, optimistic, rollback)', () => {
  it('pause is optimistic, then takes the server row', async () => {
    const d = deferred()
    apiMock.patch.mockReturnValue(d.promise)
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.findAll('[data-testid="monitor-toggle"]')[0].trigger('click')
    const row = wrapper.findAll('[data-testid="monitor-row"]')[0]
    expect(row.find('[data-testid="monitor-state"]').text()).toBe('paused') // before the response
    expect(row.find('[data-testid="monitor-toggle"]').attributes('disabled')).toBeDefined() // in flight
    expect(apiMock.patch).toHaveBeenCalledWith('/api/relays/r1/monitors/a', { paused: true })
    d.resolve({ ...A, paused: true, effective_state: 'paused' })
    await flushPromises()
    expect(wrapper.findAll('[data-testid="monitor-row"]')[0].find('[data-testid="monitor-toggle"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('resume of a system-suspended monitor keeps the suspended tag and its copy', async () => {
    const sus = monitor({ id: 'a', paused: true, status: 'suspended', suspended_reason: 'flood', effective_state: 'paused' })
    apiMock.get.mockResolvedValue({ monitors: [sus], total: 1 })
    apiMock.patch.mockResolvedValue({ ...sus, paused: false, effective_state: 'suspended' })
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.find('[data-testid="monitor-toggle"]').trigger('click')
    await flushPromises()
    const state = wrapper.find('[data-testid="monitor-state"]')
    expect(state.text()).toBe('suspended: flood')
    expect(state.attributes('title')).toContain('Ask the agent to run the monitor again')
  })

  it('rolls back a failed pause with an inline error', async () => {
    apiMock.patch.mockRejectedValue({ message: 'x', data: { detail: 'Inbound is not running for this relay' }, status: 409 })
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.findAll('[data-testid="monitor-toggle"]')[0].trigger('click')
    await flushPromises()
    const row = wrapper.findAll('[data-testid="monitor-row"]')[0]
    expect(row.find('[data-testid="monitor-state"]').text()).toBe('active')
    expect(row.find('[data-testid="monitor-error"]').exists()).toBe(true)
    expect(wrapper.emitted('changed')).toBeUndefined()
  })

  it('a 404 on pause removes the row with an "already removed" note', async () => {
    apiMock.patch.mockRejectedValue(Object.assign(new Error('Monitor not found'), { status: 404 }))
    const { wrapper } = mountList()
    await flushPromises()
    apiMock.get.mockResolvedValue({ monitors: [B], total: 1 }) // the server no longer has it
    await wrapper.findAll('[data-testid="monitor-toggle"]')[0].trigger('click')
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['b'])
    expect(wrapper.find('[data-testid="monitor-removed-note"]').text()).toContain('already removed')
  })

  it('delete removes the row at once and restores it at its position on failure', async () => {
    const d = deferred()
    apiMock.delete.mockReturnValue(d.promise)
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.findAll('[data-testid="monitor-delete"]')[0].trigger('click')
    await wrapper.find('[data-testid="monitor-delete-yes"]').trigger('click')
    expect(rowsOf(wrapper)).toEqual(['b'])
    expect(apiMock.delete).toHaveBeenCalledWith('/api/relays/r1/monitors/a')
    d.reject(new Error('nope'))
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['a', 'b'])
    expect(wrapper.findAll('[data-testid="monitor-row"]')[0].find('[data-testid="monitor-error"]').text()).toBe('nope')
  })

  it('a successful delete stays removed and a 404 on delete is treated as removed', async () => {
    apiMock.delete.mockResolvedValueOnce({ deleted: true }).mockRejectedValueOnce(Object.assign(new Error('gone'), { status: 404 }))
    const { wrapper } = mountList()
    await flushPromises()
    apiMock.get.mockResolvedValueOnce({ monitors: [B], total: 1 }).mockResolvedValue({ monitors: [], total: 0 })
    for (const id of ['a', 'b']) {
      await wrapper.find(`[data-monitor-id="${id}"] [data-testid="monitor-delete"]`).trigger('click')
      await wrapper.find(`[data-monitor-id="${id}"] [data-testid="monitor-delete-yes"]`).trigger('click')
      await flushPromises()
    }
    expect(rowsOf(wrapper)).toEqual([])
    expect(wrapper.emitted('changed')).toHaveLength(2)
  })

  it('a poll arriving during an in-flight action does not overwrite the optimistic row', async () => {
    const d = deferred()
    apiMock.patch.mockReturnValue(d.promise)
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.findAll('[data-testid="monitor-toggle"]')[0].trigger('click')
    await wrapper.vm.$.exposed.reload() // stale server state (not paused yet)
    expect(wrapper.findAll('[data-testid="monitor-row"]')[0].find('[data-testid="monitor-state"]').text()).toBe('paused')
    d.resolve({ ...A, paused: true, effective_state: 'paused' })
    await flushPromises()
  })

  it('a poll that started before a delete cannot resurrect the row', async () => {
    const slowPoll = deferred()
    const { wrapper } = mountList()
    await flushPromises()
    apiMock.get.mockReturnValueOnce(slowPoll.promise) // an in-flight poll with pre-delete state
    wrapper.vm.$.exposed.reload()
    apiMock.delete.mockResolvedValue({ deleted: true })
    apiMock.get.mockResolvedValue({ monitors: [B], total: 1 })
    await wrapper.find('[data-monitor-id="a"] [data-testid="monitor-delete"]').trigger('click')
    await wrapper.find('[data-monitor-id="a"] [data-testid="monitor-delete-yes"]').trigger('click')
    await flushPromises()
    slowPoll.resolve({ monitors: [A, B], total: 2 })
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['b'])
  })

  it('a filter change during an action reloads once the action ends', async () => {
    const d = deferred()
    apiMock.patch.mockReturnValue(d.promise)
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.findAll('[data-testid="monitor-toggle"]')[0].trigger('click')
    apiMock.get.mockResolvedValue({ monitors: [B], total: 1 })
    await wrapper.find('[data-testid="monitor-filter-suspended"]').trigger('click')
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['a', 'b']) // skipped while the action is in flight
    d.resolve({ ...A, paused: true, effective_state: 'paused' })
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['b'])
    expect(apiMock.get.mock.calls.at(-1)[1].params.status).toBe('suspended')
  })
})
