import { describe, it, expect } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import RelayMonitorRow from '@/components/relay/RelayMonitorRow.vue'
import { NOW, monitor } from './monitorFixtures'

const mountRow = (m, props = {}) => renderWithStores(RelayMonitorRow, { props: { monitor: m, nowSec: NOW, ...props } })

describe('RelayMonitorRow', () => {
  it.each([
    ['monitor_thread', { thread_ts: '1.2' }, 'thread · #ops'],
    ['monitor_channel', {}, '#ops'],
    ['monitor_mentions', { channel: null }, 'mentions of you'],
    ['monitor_dms', { channel: null }, 'direct messages'],
  ])('renders a %s monitor on one line', (kind, extra, text) => {
    const { wrapper } = mountRow(monitor({ kind, ...extra }))
    expect(wrapper.find('[data-testid="monitor-label"]').text()).toBe(text)
    expect(wrapper.find('[data-testid="monitor-state"]').text()).toBe('active')
    expect(wrapper.find('[data-testid="monitor-session"]').text()).toBe('Deploy agent !deploy')
    expect(wrapper.find('[data-testid="monitor-counts"]').text()).toBe('41 ✓ 3 ✕')
    expect(wrapper.find('[data-testid="monitor-delivered"]').text()).toBe('delivered 5 min ago')
  })

  it('shows two filter chips and +n with the rest in hover text', () => {
    const { wrapper } = mountRow(monitor({ filters: { keywords: ['a', 'b', 'c'], users: ['U0AAAAAA1'], user_labels: {} } }))
    const tags = wrapper.find('[data-testid="monitor-chips"]').findAll('.relay-tag')
    expect(tags.map(t => t.text())).toEqual(['kw: a', 'kw: b', '+2'])
    expect(tags[2].attributes('title')).toContain('keyword: c')
  })

  it('state tags: suspended reason, owner pause, expiry; copy in the hover text', () => {
    const sus = mountRow(monitor({ status: 'suspended', suspended_reason: 'flood', effective_state: 'suspended', expires_at: NOW + 2 * 86400 })).wrapper
    expect(sus.find('[data-testid="monitor-state"]').text()).toBe('suspended: flood')
    expect(sus.find('[data-testid="monitor-state"]').attributes('title')).toContain('Ask the agent to run the monitor again')
    expect(sus.find('[data-testid="monitor-expires"]').text()).toBe('expires in 2 d')
    const paused = mountRow(monitor({ paused: true, effective_state: 'paused' })).wrapper
    expect(paused.find('[data-testid="monitor-state"]').text()).toBe('paused')
    expect(paused.find('[data-testid="monitor-toggle"]').attributes('aria-label')).toBe('Resume monitor')
  })

  it('expands plain-language detail: consent, caught up to, failures, session link', async () => {
    const m = monitor({
      kind: 'monitor_mentions', channel: null, consent: { at: NOW - 100, via: 'prompt' }, failures: 2, last_error: 'channel_not_found',
      status: 'suspended', suspended_reason: 'delivery_failed', effective_state: 'suspended',
    })
    const { wrapper } = mountRow(m)
    expect(wrapper.find('[data-testid="monitor-detail"]').exists()).toBe(false)
    await wrapper.find('[data-testid="monitor-expand"]').trigger('click')
    const d = wrapper.find('[data-testid="monitor-detail"]')
    expect(d.find('[data-testid="detail-consent"]').text()).toMatch(/You allowed this on .* \(prompt\)/)
    expect(d.find('[data-testid="detail-progress"]').text()).toContain('caught up to 2 min ago')
    expect(d.find('[data-testid="detail-progress"]').text()).not.toMatch(/cursor/i)
    expect(d.find('[data-testid="detail-failures"]').text()).toContain('2 failed deliveries in a row')
    expect(d.find('[data-testid="detail-failures"]').text()).toContain('channel_not_found')
    expect(d.find('[data-testid="detail-state"]').text()).toContain('repeated delivery failures')
    expect(d.find('a').attributes('href')).toBe('/session/s1')
  })

  it('renders hostile Slack labels as text, never markup', () => {
    const evil = '<img src=x onerror=alert(1)> **bold**'
    const { wrapper } = mountRow(monitor({
      channel_label: evil, session_name: evil, filters: { keywords: [evil], users: [], user_labels: {} },
    }))
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('[data-testid="monitor-label"]').text()).toContain('<img src=x')
  })

  it('actions are icon-only with titles; delete asks inline', async () => {
    const { wrapper } = mountRow(monitor())
    const toggle = wrapper.find('[data-testid="monitor-toggle"]')
    expect(toggle.attributes('data-icon')).toBe('pause')
    expect(toggle.find('svg').exists()).toBe(true)
    expect(toggle.attributes('title')).toContain('Applies immediately')
    expect(wrapper.find('[data-testid="monitor-delete"]').attributes('title')).toContain('Applies immediately')
    await toggle.trigger('click')
    expect(wrapper.emitted('toggle-pause')[0][0].id).toBe('m1')
    await wrapper.find('[data-testid="monitor-delete"]').trigger('click')
    expect(wrapper.find('[data-testid="monitor-confirm"]').text()).toBe('Delete?')
    await wrapper.find('[data-testid="monitor-delete-no"]').trigger('click')
    expect(wrapper.find('[data-testid="monitor-confirm"]').exists()).toBe(false)
    await wrapper.find('[data-testid="monitor-delete"]').trigger('click')
    await wrapper.find('[data-testid="monitor-delete-yes"]').trigger('click')
    expect(wrapper.emitted('delete')[0][0].id).toBe('m1')
  })

  it('disables the actions while a request is in flight and shows an inline error', () => {
    const { wrapper } = mountRow(monitor(), { busy: true, error: 'boom' })
    expect(wrapper.find('[data-testid="monitor-toggle"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="monitor-delete"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="monitor-error"]').text()).toBe('boom')
  })

  it('hides the session cell when locked to one session', () => {
    const { wrapper } = mountRow(monitor(), { showSession: false })
    expect(wrapper.find('[data-testid="monitor-session"]').exists()).toBe(false)
  })
})
