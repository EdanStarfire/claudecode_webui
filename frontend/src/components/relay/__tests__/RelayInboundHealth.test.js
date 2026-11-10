import { describe, it, expect } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import RelayInboundHealth from '@/components/relay/RelayInboundHealth.vue'
import { NOW, health } from './monitorFixtures'

const mountHealth = (h) => renderWithStores(RelayInboundHealth, {
  props: { health: h, nowSec: NOW }, routes: [{ path: '/settings/relays', component: { template: '<div/>' } }, { path: '/', component: { template: '<div/>' } }],
})

describe('RelayInboundHealth', () => {
  it('shows three one-line facts without any box', () => {
    const { wrapper } = mountHealth(health({ inflight: 2 }))
    expect(wrapper.find('[data-testid="health-status"]').text()).toBe('connected')
    expect(wrapper.find('[data-testid="health-since"]').text()).toBe('for 1 h')
    expect(wrapper.find('[data-testid="health-inflight"]').text()).toBe('2 in flight')
    expect(wrapper.find('[data-testid="health-monitors"]').text()).toContain('2 active · 1 paused')
    expect(wrapper.find('[data-testid="health-window"]').text()).toContain('delivered 41 msgs · dropped 3')
    expect(wrapper.find('[data-testid="health-attention"]').exists()).toBe(false)
    expect(wrapper.find('.alert').exists()).toBe(false)
  })

  it('prefixes "at least" when the audit scan was truncated', () => {
    const w = health().window_24h
    const { wrapper } = mountHealth(health({ window_24h: { ...w, truncated: true } }))
    expect(wrapper.find('[data-testid="health-window"]').text()).toContain('at least delivered 41 msgs')
  })

  it('hover text states what is since restart / from the audit', () => {
    const { wrapper } = mountHealth(health({ last_backfill: { at: NOW - 60, gap: true, counts: {} }, last_backfill_audit: { ts: new Date((NOW - 90) * 1000).toISOString(), code: 'gap_notice' } }))
    const title = wrapper.find('[data-testid="health-window"] .fact-v, [data-testid="health-window"]').find('.fact-v').attributes('title')
    expect(title).toContain('survive a restart')
    expect(title).toContain('since restart')
    expect(title).toContain('(incomplete)')
    expect(wrapper.find('[data-testid="health-connection"] .soft').attributes('title')).toContain('Backend restart')
  })

  it('amber tags for attention items, neutral for info, each with an audit link', () => {
    const { wrapper } = mountHealth(health({
      status: 'auth_failed',
      attention: [
        { code: 'auth_failed', severity: 'attention', since: null, count: 1, audit: { events: ['inbound_connection'] } },
        { code: 'monitors_flood', severity: 'attention', count: 2, session_ids: ['s1'], audit: { events: ['monitor_flood', 'monitor_suspended'], session_id: 's1' } },
        { code: 'monitors_paused', severity: 'info', count: 1, audit: { events: ['monitor_paused'] } },
      ],
    }))
    const tags = wrapper.findAll('[data-testid="health-tag"]')
    expect(tags.map(t => t.attributes('data-code'))).toEqual(['auth_failed', 'monitors_flood', 'monitors_paused'])
    expect(tags[0].find('.relay-tag').classes()).toContain('relay-tag--amber')
    expect(tags[2].find('.relay-tag').classes()).not.toContain('relay-tag--amber')
    expect(tags[0].attributes('title')).toContain('Update it in Setup')
    expect(tags[1].find('.relay-tag').text()).toBe('flood ×2')
    expect(tags[1].find('a').attributes('href')).toBe('/settings/relays?tab=audit&events=monitor_flood,monitor_suspended&session=s1')
    expect(wrapper.find('[data-testid="health-status"]').classes()).toContain('chip--bad')
  })
})
