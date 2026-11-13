import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderWithStores } from '@/test-utils/render'
import RelayMonitorList from '@/components/relay/RelayMonitorList.vue'
import { NOW, digest, monitor, unreadMonitor } from './monitorFixtures'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const mountList = (props = {}) => renderWithStores(RelayMonitorList, { props: { relayId: 'r1', ...props } })
const rowsOf = (w) => w.findAll('[data-testid="monitor-row"]').map(r => r.attributes('data-monitor-id'))
const apiError = (status, detail) => Object.assign(new Error('x'), { status, data: { detail } })
const D = digest()

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ monitors: [D], total: 1 })
})
afterEach(() => vi.useRealTimers())

describe('Type filter, footer and sort', () => {
  it('sends mode and says digests repeat live messages', async () => {
    const { wrapper } = mountList()
    await flushPromises()
    expect(wrapper.find('[data-testid="monitor-repeat-foot"]').text()).toBe('Digests and live monitors on the same scope repeat the same messages.')
    await wrapper.find('[data-testid="monitor-filter-mode"]').setValue('digest')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.mode).toBe('digest')
    await wrapper.find('[data-testid="monitor-filter-mode"]').setValue('')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.mode).toBe(null)
  })

  it('no repeat sentence when no scheduled monitor is listed', async () => {
    apiMock.get.mockResolvedValue({ monitors: [monitor()], total: 1 })
    const { wrapper } = mountList()
    await flushPromises()
    expect(wrapper.find('[data-testid="monitor-repeat-foot"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="monitor-foot"]').text()).toContain('Applies immediately')
  })

  it('sorts the loaded page by next run, live monitors last', async () => {
    apiMock.get.mockResolvedValue({ monitors: [
      monitor({ id: 'live' }), digest({ id: 'late', next_run: NOW + 9000 }), unreadMonitor({ id: 'soon', next_run: NOW + 60 }),
    ], total: 3 })
    const { wrapper } = mountList()
    await flushPromises()
    expect(rowsOf(wrapper)).toEqual(['live', 'late', 'soon'])
    await wrapper.find('[data-testid="monitor-sort"]').setValue('next')
    expect(rowsOf(wrapper)).toEqual(['soon', 'late', 'live'])
  })

  it('shows the repeat notice in a digest row whose live twin is listed', async () => {
    apiMock.get.mockResolvedValue({ monitors: [D, monitor({ id: 'live' })], total: 2 })
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.find('[data-monitor-id="d1"] [data-testid="monitor-expand"]').trigger('click')
    expect(wrapper.find('[data-monitor-id="d1"] [data-testid="detail-repeat"]').exists()).toBe(true)
    expect(wrapper.find('[data-monitor-id="live"] [data-testid="detail-repeat"]').exists()).toBe(false)
  })
})

describe('Run now', () => {
  const click = async (w) => { await w.find('[data-testid="monitor-run"]').trigger('click'); await flushPromises() }

  it('posts to the relay run route, shows Running… and the info note, then ends on a new last run', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval'] })
    apiMock.post.mockResolvedValue({ started: true, monitor: D })
    const { wrapper } = mountList()
    await flushPromises()
    await click(wrapper)
    expect(apiMock.post.mock.calls[0][0]).toBe('/api/relays/r1/monitors/d1/run')
    expect(wrapper.find('[data-testid="sched-running"]').text()).toBe('· Running…')
    expect(wrapper.find('[data-testid="monitor-note"]').text()).toContain('Started')
    expect(wrapper.find('[data-testid="monitor-run"]').attributes('disabled')).toBeDefined()
    apiMock.get.mockResolvedValue({ monitors: [digest({ last_run: { at: NOW + 5, outcome: 'empty', messages: 0 } })], total: 1 })
    await vi.advanceTimersByTimeAsync(2600)
    await flushPromises()
    expect(wrapper.find('[data-testid="sched-running"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="sched-last"]').text()).toContain('empty')
    expect(wrapper.find('[data-testid="monitor-note"]').exists()).toBe(false)
  })

  it('ends Running… after 8 s at the latest', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval'] })
    apiMock.post.mockResolvedValue({ started: true, monitor: D })
    const { wrapper } = mountList()
    await flushPromises()
    await click(wrapper)
    await vi.advanceTimersByTimeAsync(8100)
    await flushPromises()
    expect(wrapper.find('[data-testid="sched-running"]').exists()).toBe(false)
  })

  it('uses the session-qualified route in a locked list', async () => {
    apiMock.post.mockResolvedValue({ started: true, monitor: D })
    const { wrapper } = mountList({ sessionId: 's1' })
    await flushPromises()
    await click(wrapper)
    expect(apiMock.post.mock.calls[0][0]).toBe('/api/sessions/s1/relays/r1/monitors/d1/run')
  })

  it('does not double submit', async () => {
    let resolve
    apiMock.post.mockReturnValue(new Promise(r => { resolve = r }))
    const { wrapper } = mountList()
    await flushPromises()
    await wrapper.find('[data-testid="monitor-run"]').trigger('click')
    await wrapper.find('[data-testid="monitor-run"]').trigger('click')
    expect(apiMock.post).toHaveBeenCalledTimes(1)
    resolve({ started: true, monitor: D })
    await flushPromises()
  })

  it.each([
    [429, { code: 'run_too_soon', message: 'm', retry_after_seconds: 41 }, 'Please wait 41 s before running it again.'],
    [409, { code: 'monitor_paused', message: 'm' }, 'Paused: resume it first.'],
    [409, { code: 'monitor_suspended', message: 'm' }, 'Suspended by the system'],
    [409, { code: 'run_in_progress', message: 'm' }, 'already in progress'],
    [409, { code: 'inbound_unavailable', message: 'm' }, 'switched off'],
  ])('%i %j is shown as a plain sentence', async (status, detail, text) => {
    apiMock.post.mockRejectedValue(apiError(status, detail))
    const { wrapper } = mountList()
    await flushPromises()
    await click(wrapper)
    expect(wrapper.find('[data-testid="monitor-error"]').text()).toContain(text)
    expect(wrapper.find('[data-testid="sched-running"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="monitor-run"]').attributes('disabled')).toBeUndefined()
  })

  it('404 removes the row; 422 shows nothing', async () => {
    apiMock.post.mockRejectedValue(apiError(422, { code: 'not_a_digest_monitor', message: 'm' }))
    const { wrapper } = mountList()
    await flushPromises()
    await click(wrapper)
    expect(wrapper.find('[data-testid="monitor-error"]').exists()).toBe(false)
    apiMock.post.mockRejectedValue(apiError(404, 'Monitor not found'))
    await click(wrapper)
    expect(rowsOf(wrapper)).toEqual([])
    expect(wrapper.find('[data-testid="monitor-removed-note"]').text()).toContain('already removed')
  })
})
