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

describe('RelayInboundHealth scheduled monitors (3c-iii)', () => {
  it('shows scheduled count with the next run and the 24 h digest line', () => {
    const { wrapper } = mountHealth(health({
      monitors: { total: 3, active: 3, paused: 0, suspended: 0, by_reason: {}, scheduled: 2, next_run: NOW + 720 },
      window_24h: { ...health().window_24h, digest_delivered: 5, digest_empty: 2, rate_limited_runs: 1, late_runs: 0 },
    }))
    expect(wrapper.find('[data-testid="health-monitors"]').text()).toContain('2 scheduled (next run in 12 min)')
    expect(wrapper.find('[data-testid="health-digests"]').text()).toContain('digests 5 delivered · 2 empty · rate limited ×1')
  })

  it('hides the digest line when nothing ran; older Backend renders', () => {
    expect(mountHealth(health()).wrapper.find('[data-testid="health-digests"]').exists()).toBe(false)
  })

  it('new attention and info tags with audit links to monitor_digest', () => {
    const { wrapper } = mountHealth(health({ attention: [
      { code: 'digest_failures', severity: 'attention', count: 1, audit: { events: ['monitor_digest'], session_id: 's1' } },
      { code: 'digest_rate_limited_24h', severity: 'attention', count: 2, audit: { events: ['monitor_digest'] } },
      { code: 'digest_overdue', severity: 'attention', count: 1, audit: { events: ['monitor_digest'] } },
      { code: 'digest_ran_late', severity: 'info', count: 1, audit: { events: ['monitor_digest'] } },
      { code: 'digest_deferred', severity: 'info', count: 1, audit: { events: ['monitor_digest'] } },
    ] }))
    const tags = wrapper.findAll('[data-testid="health-tag"]')
    expect(tags.map(t => t.find('.relay-tag').text())).toEqual(['digest failing', 'rate limited ×2', 'run overdue', 'ran late', 'postponed'])
    expect(tags[0].find('.relay-tag').classes()).toContain('relay-tag--amber')
    expect(tags[3].find('.relay-tag').classes()).not.toContain('relay-tag--amber')
    expect(tags[0].find('a').attributes('href')).toBe('/settings/relays?tab=audit&events=monitor_digest&session=s1')
    expect(tags[0].attributes('title')).toContain('failed 3 times in a row')
  })
})
