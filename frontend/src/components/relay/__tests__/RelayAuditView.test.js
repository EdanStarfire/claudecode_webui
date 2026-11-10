import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderWithStores } from '@/test-utils/render'
import RelayAuditView from '@/components/relay/RelayAuditView.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const REC = (over) => ({ ts: '2026-01-01T00:00:00+00:00', event: 'tool', session_id: 's1', session_name: 'Agent', tool: 'post', channel_id: 'C0AAAAAAA1', outcome: 'ok', ...over })
const ROUTES = [{ path: '/settings/relays', component: { template: '<div/>' } }]

async function mountView(query = '') {
  const url = `/settings/relays${query}`
  const rendered = renderWithStores(RelayAuditView, { props: { relayId: 'r1' }, routes: ROUTES, initialRoute: url })
  await rendered.router.push(url)
  await flushPromises()
  return rendered
}

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ records: [] })
})

describe('RelayAuditView', () => {
  it('renders every action type: tool names, identity/discovery, policy change, denials', async () => {
    apiMock.get.mockResolvedValue({ records: [
      REC({ tool: 'post', text_preview: 'hello <b>x</b>' }),
      REC({ tool: 'reply' }), REC({ tool: 'upload_file' }), REC({ tool: 'react' }), REC({ tool: 'mark_read' }),
      REC({ tool: 'get_identity' }),
      REC({ tool: 'find_channels', query_preview: 'dev', kinds: ['public', 'dm'], result_count: 3 }),
      REC({ event: 'policy_change', tool: undefined, policy: 'auto' }),
      REC({ tool: 'post', outcome: 'denied', code: 'self_dm_unresolved' }),
      REC({ tool: 'mcp__relay_slack__search' }),
    ] })
    const { wrapper } = await mountView()
    const actions = wrapper.findAll('[data-testid="audit-action"]').map(a => a.text())
    expect(actions).toEqual(['Post', 'Reply', 'Upload', 'React', 'Mark read', 'Get identity',
      'Find channels', 'Policy change', 'Post', 'Search'])
    const details = wrapper.findAll('[data-testid="audit-detail"]').map(d => d.text())
    expect(details[6]).toBe('“dev” · public, dm · 3 results')
    expect(details[7]).toBe('→ auto')
    const denied = wrapper.findAll('[data-testid="audit-record"]')[8]
    expect(denied.text()).toContain('denied · self_dm_unresolved')
    expect(denied.classes()).toContain('outcome-denied')
  })

  it('humanizes the inbound events and keeps their outcome styling (stage 3a)', async () => {
    const E = (over) => REC({ tool: undefined, ...over })
    apiMock.get.mockResolvedValue({ records: [
      E({ event: 'inbound_instruction', outcome: 'delivered', text_preview: 'run the checks' }),
      E({ event: 'inbound_instruction', outcome: 'refused', code: 'unknown_handle' }),
      E({ event: 'inbound_instruction', outcome: 'late', code: 'received_late' }),
      E({ event: 'inbound_instruction', outcome: 'rate_limited', code: 'rate_limited' }),
      E({ event: 'inbound_instruction', outcome: 'deduped' }),
      E({ event: 'inbound_reply', outcome: 'ok', code: 'unknown_handle' }),
      E({ event: 'inbound_reply_failed', outcome: 'error', code: 'unknown_handle' }),
      E({ event: 'inbound_connection', outcome: null, inbound_status: 'auth_failed', code: 'token_revoked' }),
      E({ event: 'inbound_drop_summary', outcome: 'ok', count: 3, reason_counts: { not_owner: 2, deduped: 1 } }),
      E({ event: 'inbound_housekeeping', outcome: 'ok', code: 'expired', count: 4 }),
    ] })
    const { wrapper } = await mountView()
    const actions = wrapper.findAll('[data-testid="audit-action"]').map(a => a.text())
    expect(actions).toEqual(['Slack instruction', 'Slack instruction', 'Slack instruction', 'Slack instruction',
      'Slack instruction', 'Slack reply', 'Slack reply', 'Inbound connection', 'Inbound drops', 'Inbound housekeeping'])
    expect(actions.join(' ')).not.toMatch(/inbound_/)
    const rows = wrapper.findAll('[data-testid="audit-record"]')
    expect(rows[1].text()).toContain('refused · unknown_handle')
    expect(rows[1].classes()).toContain('outcome-refused')
    expect(rows[2].classes()).toContain('outcome-late')
    expect(rows[3].classes()).toContain('outcome-rate_limited')
    expect(rows[6].classes()).toContain('outcome-error')
    const details = wrapper.findAll('[data-testid="audit-detail"]').map(d => d.text())
    expect(details[0]).toBe('run the checks')
    expect(details[7]).toBe('auth_failed')
    expect(details[8]).toBe('not_owner 2 · deduped 1')
    expect(details[9]).toBe('4')
    expect(rows[7].find('.outcome').text()).toBe('token_revoked') // no leading separator without an outcome
  })

  it('humanizes the monitor events with kind + count or reason, never message text (3b-i)', async () => {
    const E = (over) => REC({ tool: undefined, ...over })
    apiMock.get.mockResolvedValue({ records: [
      E({ event: 'monitor_created', outcome: 'ok', monitor_kind: 'monitor_channel', count: 2, source: 'agent' }),
      E({ event: 'monitor_removed', outcome: 'ok', monitor_kind: 'monitor_thread', source: 'expired' }),
      E({ event: 'monitor_suspended', outcome: 'ok', code: 'channel_blocked', monitor_kind: 'monitor_thread' }),
      E({ event: 'monitor_resumed', outcome: 'ok', source: 'policy', monitor_kind: 'monitor_thread' }),
      E({ event: 'monitor_delivered', outcome: 'ok', count: 3, omitted: 10 }),
      E({ event: 'monitor_delivery_failed', outcome: 'error', code: 'cancelled' }),
      E({ event: 'monitor_expired', outcome: 'ok', code: 'idle', monitor_kind: 'monitor_thread' }),
      E({ event: 'monitor_flood', outcome: 'dropped', code: 'flood_limit', monitor_kind: 'monitor_channel' }),
      E({ event: 'monitor_backfill', outcome: 'truncated', code: 'backfill_truncated', count: 300 }),
      E({ event: 'inbound_instruction', outcome: 'delivery_failed', code: 'delivery_failed' }),
      E({ event: 'inbound_instruction', outcome: 'cancelled', code: 'cancelled' }),
    ] })
    const { wrapper } = await mountView()
    const actions = wrapper.findAll('[data-testid="audit-action"]').map(a => a.text())
    expect(actions).toEqual(['Monitor created', 'Monitor removed', 'Monitor suspended', 'Monitor resumed',
      'Monitor delivered', 'Monitor delivery failed', 'Monitor expired', 'Monitor flood', 'Monitor backfill',
      'Slack instruction', 'Slack instruction'])
    expect(actions.join(' ')).not.toMatch(/monitor_/)
    const details = wrapper.findAll('[data-testid="audit-detail"]').map(d => d.text())
    expect(details[0]).toBe('channel monitor · 2 keywords · by agent')
    expect(details[1]).toBe('thread monitor · by expired')
    expect(details[2]).toBe('thread monitor · channel_blocked')
    expect(details[3]).toBe('thread monitor · by policy')
    expect(details[4]).toBe('3 messages · +10 omitted')
    expect(details[5]).toBe('cancelled')
    expect(details[6]).toBe('thread monitor · idle')
    expect(details[7]).toBe('channel monitor · over the flood limit, messages dropped')
    expect(details[8]).toBe('300 messages')
    const rows = wrapper.findAll('[data-testid="audit-record"]')
    expect(rows[5].classes()).toContain('outcome-error')
    expect(rows[7].classes()).toContain('outcome-dropped')
    expect(rows[9].classes()).toContain('outcome-delivery_failed')
    expect(rows[10].classes()).toContain('outcome-cancelled')
  })

  it('humanizes the received-reaction events (#2364)', async () => {
    const E = (over) => REC({ tool: undefined, event: 'inbound_ack', ...over })
    apiMock.get.mockResolvedValue({ records: [
      E({ outcome: 'ok' }),
      E({ outcome: 'error', code: 'message_not_found' }),
      E({ outcome: 'removed' }),
    ] })
    const { wrapper } = await mountView()
    const actions = wrapper.findAll('[data-testid="audit-action"]').map(a => a.text())
    expect(actions).toEqual(['Slack acknowledgement', 'Slack acknowledgement', 'Slack acknowledgement'])
    const rows = wrapper.findAll('[data-testid="audit-record"]')
    expect(rows[1].text()).toContain('error · message_not_found')
    expect(rows[1].classes()).toContain('outcome-error')
    expect(rows[2].classes()).toContain('outcome-removed')
  })

  it('policy changes show old → new with (none) and a quiet source/tier marker (#2355)', async () => {
    const P = (over) => REC({ event: 'policy_change', tool: undefined, ...over })
    apiMock.get.mockResolvedValue({ records: [
      P({ source: 'editor', tier: 'session', old_policy: null, policy: 'auto' }),
      P({ source: 'config', tier: 'session', old_policy: 'auto', policy: 'blocked' }),
      P({ source: 'editor', tier: 'session', old_policy: 'blocked', policy: null }),
      P({ source: 'prompt', old_policy: null, policy: 'auto' }),
      P({ source: 'template', tier: 'template', tier_name: 'Planner tmpl', session_id: null, old_policy: 'ask', policy: 'read_only' }),
      P({ source: 'profile', tier: 'profile', tier_name: 'Base', session_id: null, old_policy: 'ask', policy: 'blocked' }),
      P({ policy: 'auto' }),
    ] })
    const { wrapper } = await mountView()
    const details = wrapper.findAll('[data-testid="audit-detail"]').map(d => d.text())
    expect(details).toEqual([
      '(none) → auto', 'auto → blocked', 'blocked → (none)', '→ auto',
      'ask → read_only', 'ask → blocked', '→ auto',
    ])
    const tags = wrapper.findAll('[data-testid="audit-record"]').map(r => r.find('[data-testid="audit-source"]').exists() ? r.find('[data-testid="audit-source"]').text() : null)
    expect(tags).toEqual(['editor', 'config', 'editor', 'prompt', 'template: Planner tmpl', 'profile: Base', null])
  })

  it('escapes Slack-controlled previews and caps them', async () => {
    apiMock.get.mockResolvedValue({ records: [REC({ text_preview: '<img src=x onerror=1>' + 'a'.repeat(400) })] })
    const { wrapper } = await mountView()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('[data-testid="audit-detail"]').text().length).toBe(200)
  })

  it('channel shows the name, else the ID', async () => {
    apiMock.get.mockResolvedValue({ records: [REC({ channel_name: 'general' }), REC({})] })
    const { wrapper } = await mountView()
    const cells = wrapper.findAll('[data-label="Channel"]').map(c => c.text())
    expect(cells).toEqual(['general', 'C0AAAAAAA1'])
  })

  it('deep-link query params seed the server filters', async () => {
    vi.useFakeTimers()
    await mountView('?session=s9&channel=C0ZZZZZZZ1')
    await vi.advanceTimersByTimeAsync(300)
    vi.useRealTimers()
    const [url, opts] = apiMock.get.mock.calls.at(-1)
    expect(url).toBe('/api/relays/r1/audit')
    expect(opts.params).toMatchObject({ session_id: 's9', channel: 'C0ZZZZZZZ1' })
  })

  it('session and channel filters re-query; outcome filters client-side', async () => {
    vi.useFakeTimers()
    apiMock.get.mockResolvedValue({ records: [REC({}), REC({ outcome: 'denied' })] })
    const { wrapper, pinia } = await mountView()
    const { useSessionStore } = await import('@/stores/session')
    useSessionStore(pinia).sessions.set('s1', makeSession({ session_id: 's1', name: 'Agent' }))
    await wrapper.vm.$nextTick()
    apiMock.get.mockClear()
    await wrapper.find('[data-testid="audit-session"]').setValue('s1')
    await vi.advanceTimersByTimeAsync(300)
    expect(apiMock.get.mock.calls[0][1].params.session_id).toBe('s1')
    apiMock.get.mockClear()
    await wrapper.find('[data-testid="audit-outcome"]').setValue('denied')
    expect(apiMock.get).not.toHaveBeenCalled()
    expect(wrapper.findAll('[data-testid="audit-record"]').length).toBe(1)
    vi.useRealTimers()
  })

  it('Load older passes the oldest ts as the exclusive cursor and stops on a short page', async () => {
    const page = Array.from({ length: 100 }, (_, i) => REC({ ts: `2026-01-01T00:00:${String(99 - i).padStart(2, '0')}+00:00` }))
    apiMock.get.mockResolvedValueOnce({ records: page })
    const { wrapper } = await mountView()
    apiMock.get.mockResolvedValueOnce({ records: [REC({ ts: '2025-12-31T00:00:00+00:00' })] })
    await wrapper.find('[data-testid="audit-older"]').trigger('click')
    await flushPromises()
    expect(apiMock.get.mock.calls.at(-1)[1].params.before).toBe('2026-01-01T00:00:00+00:00')
    expect(wrapper.findAll('[data-testid="audit-record"]').length).toBe(101)
    expect(wrapper.find('[data-testid="audit-older"]').exists()).toBe(false)
  })

  it('uses one table with labelled cells (phone CSS turns rows into cards) and shows the empty state', async () => {
    apiMock.get.mockResolvedValue({ records: [REC({})] })
    const { wrapper } = await mountView()
    expect(wrapper.findAll('td').every(td => td.attributes('data-label'))).toBe(true)
    apiMock.get.mockResolvedValue({ records: [] })
    const second = await mountView()
    expect(second.wrapper.find('[data-testid="audit-empty"]').exists()).toBe(true)
  })
})
