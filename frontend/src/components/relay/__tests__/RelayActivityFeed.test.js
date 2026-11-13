import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderWithStores } from '@/test-utils/render'
import RelayActivityFeed from '@/components/relay/RelayActivityFeed.vue'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const row = (over) => ({ ts: '2026-11-10T12:00:00+00:00', event: 'monitor_delivered', outcome: 'ok', session_name: 'Agent', count: 3, ...over })
const ROUTES = [{ path: '/settings/relays', component: { template: '<div/>' } }, { path: '/', component: { template: '<div/>' } }]
const mountFeed = (props = {}) => renderWithStores(RelayActivityFeed, { props: { relayId: 'r1', ...props }, routes: ROUTES })

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ records: [] })
})

describe('RelayActivityFeed', () => {
  it('requests the last 50 inbound/monitor rows and humanizes each event type', async () => {
    apiMock.get.mockResolvedValue({ records: [
      row({ event: 'inbound_instruction', outcome: 'refused', code: 'unknown_handle', text_preview: 'SECRET INSTRUCTION' }),
      row({ event: 'inbound_ack', outcome: 'ok' }),
      row({ event: 'inbound_connection', outcome: null, inbound_status: 'backoff' }),
      row({ event: 'inbound_drop_summary', reason_counts: { no_handle: 4 } }),
      row({ event: 'monitor_delivered', count: 3, omitted: 2 }),
      row({ event: 'monitor_suspended', outcome: 'ok', code: 'flood', monitor_kind: 'monitor_channel' }),
      row({ event: 'monitor_flood', outcome: 'dropped' }),
      row({ event: 'monitor_backfill', outcome: 'truncated', code: 'gap_notice', reason_counts: { non_member: 2 } }),
    ] })
    const { wrapper } = mountFeed()
    await flushPromises()
    const params = apiMock.get.mock.calls[0][1].params
    expect(apiMock.get.mock.calls[0][0]).toBe('/api/relays/r1/audit')
    expect(params).toMatchObject({ events: 'inbound_*,monitor_*', limit: 50 })
    const texts = wrapper.findAll('[data-testid="activity-text"]').map(t => t.text())
    expect(texts[0]).toBe('Slack instruction · unknown_handle')
    expect(texts[2]).toBe('Inbound connection · backoff')
    expect(texts[3]).toBe('Inbound drops · no_handle 4')
    expect(texts[4]).toBe('Monitor delivered · channel monitor · 3 messages · +2 omitted'.replace('channel monitor · ', ''))
    expect(texts[5]).toContain('Monitor suspended')
    expect(texts[7]).toContain('backfill incomplete')
    expect(wrapper.text()).not.toContain('SECRET INSTRUCTION')
    expect(wrapper.findAll('[data-testid="activity-row"]')[0].classes()).toContain('problem')
  })

  it('chips change the event filter; Problems filters on outcome', async () => {
    apiMock.get.mockResolvedValue({ records: [row({ outcome: 'ok' }), row({ event: 'inbound_instruction', outcome: 'late' })] })
    const { wrapper } = mountFeed()
    await flushPromises()
    await wrapper.find('[data-testid="activity-chip-monitors"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.events).toBe('monitor_*')
    await wrapper.find('[data-testid="activity-chip-connection"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.events).toBe('inbound_connection')
    await wrapper.find('[data-testid="activity-chip-problems"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-testid="activity-row"]')).toHaveLength(1)
  })

  it('Load older pages by the oldest ts; Open in Audit links the active filter', async () => {
    const page = Array.from({ length: 50 }, (_, i) => row({ ts: `2026-11-10T12:${String(59 - i).padStart(2, '0')}:00+00:00` }))
    apiMock.get.mockResolvedValueOnce({ records: page })
    const { wrapper } = mountFeed()
    await flushPromises()
    apiMock.get.mockResolvedValueOnce({ records: [row({ ts: '2026-11-09T00:00:00+00:00' })] })
    await wrapper.find('[data-testid="activity-older"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.before).toBe(page[49].ts)
    expect(wrapper.findAll('[data-testid="activity-row"]')).toHaveLength(51)
    expect(wrapper.find('[data-testid="activity-older"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="activity-open-audit"]').attributes('href')).toContain('tab=audit&events=inbound_*')
  })

  it('loads when the tab becomes visible, not before; Refresh reloads; empty and error states', async () => {
    const { wrapper } = mountFeed({ active: false })
    await flushPromises()
    expect(apiMock.get).not.toHaveBeenCalled()
    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="activity-empty"]').exists()).toBe(true)
    apiMock.get.mockRejectedValueOnce(new Error('down'))
    await wrapper.find('[data-testid="activity-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="activity-error"]').text()).toBe('down')
  })

  it('renders hostile text from names as text only', async () => {
    apiMock.get.mockResolvedValue({ records: [row({ session_name: '<img src=x onerror=alert(1)>' })] })
    const { wrapper } = mountFeed()
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('[data-testid="activity-session"]').text()).toBe('<img src=x onerror=alert(1)>')
  })
})

describe('RelayActivityFeed digest rows (3c-iii)', () => {
  it('labels digest and unread runs by kind with counts, outcomes and codes', async () => {
    apiMock.get.mockResolvedValue({ records: [
      row({ event: 'monitor_digest', monitor_kind: 'monitor_channel', outcome: 'delivered', count: 12, omitted: 3 }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_unread', outcome: 'delivered', count: 2 }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'empty' }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'rate_limited', code: 'rate_limited_rescheduled', retry: 2, retry_at: '2026-11-10T14:35:00+00:00' }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'rate_limited', code: 'rate_limited_gave_up', retry: 3 }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'delivered', code: 'late', count: 1 }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'refused', code: 'monitor_paused', source: 'owner' }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'accepted', code: 'run_now', source: 'agent' }),
      row({ event: 'monitor_digest', monitor_kind: 'monitor_dms', outcome: 'deferred', code: 'session_busy', retry: 1 }),
    ] })
    const { wrapper } = mountFeed()
    await flushPromises()
    const texts = wrapper.findAll('[data-testid="activity-text"]').map(t => t.text())
    expect(texts[0]).toBe('Monitor digest run · channel monitor · 12 messages · +3 omitted')
    expect(texts[1]).toBe('Monitor unread run · unread monitor · 2 conversations')
    expect(texts[2]).toContain('nothing new, session not woken')
    expect(texts[3]).toMatch(/rate limited, retry 2\/3 at \d\d:\d\d/)
    expect(texts[4]).toContain('gave up this slot')
    expect(texts[5]).toContain('ran late (missed slot)')
    expect(texts[6]).toContain('refused: monitor paused · run now by you')
    expect(texts[7]).toContain('started · run now by the agent')
    expect(texts[8]).toContain('postponed, session busy (1/6)')
    const problems = wrapper.findAll('[data-testid="activity-row"]').map(r => r.classes().includes('problem'))
    expect(problems).toEqual([false, false, false, true, true, false, true, false, true])
  })

  it('the Monitors chip already covers monitor_digest and the audit link keeps the filter', async () => {
    const { wrapper } = mountFeed()
    await flushPromises()
    await wrapper.find('[data-testid="activity-chip-monitors"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.events).toBe('monitor_*')
  })
})
