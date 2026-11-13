import { describe, it, expect } from 'vitest'
import {
  ATTENTION_COPY, STATE_COPY, agoText, attentionHint, attentionLabel, auditQuery, caughtUpTo, deliveredText,
  expiresText, filterChips, hasAttention, isProblem, monitorLabel, monitorsLine, stateCopy, stateTag, toEpoch,
  windowLine, monitorCountText, summaryMonitors, summaryTitle } from '@/utils/relayInbound'
import { NOW, health, monitor } from '@/components/relay/__tests__/monitorFixtures'

describe('stateTag / stateCopy', () => {
  it('active, paused and each suspension reason with the right tone', () => {
    expect(stateTag(monitor())).toEqual({ label: 'active', tone: 'muted' })
    expect(stateTag(monitor({ paused: true, effective_state: 'paused' }))).toEqual({ label: 'paused', tone: 'neutral' })
    const sus = (reason) => stateTag(monitor({ status: 'suspended', suspended_reason: reason, effective_state: 'suspended' }))
    expect(sus('flood')).toEqual({ label: 'suspended: flood', tone: 'amber' })
    expect(sus('delivery_failed').tone).toBe('amber')
    expect(sus('policy_unset')).toEqual({ label: 'suspended: no policy', tone: 'amber' })
    for (const r of ['channel_blocked', 'disposed', 'session_inactive']) expect(sus(r).tone).toBe('neutral')
    expect(sus('channel_blocked').label).toBe('suspended: channel blocked')
  })

  it('has plain copy for every reason; a paused+suspended monitor explains the suspension', () => {
    for (const r of ['channel_blocked', 'policy_unset', 'session_inactive', 'disposed', 'flood', 'delivery_failed']) {
      expect(STATE_COPY[r]).toBeTruthy()
      expect(stateCopy(monitor({ status: 'suspended', suspended_reason: r }))).toBe(STATE_COPY[r])
    }
    const both = stateCopy(monitor({ status: 'suspended', suspended_reason: 'flood', paused: true }))
    expect(both).toContain(STATE_COPY.flood)
    expect(both).toContain('also paused')
    expect(stateCopy(monitor({ paused: true }))).toBe(STATE_COPY.paused)
    expect(Object.values(STATE_COPY).join(' ')).not.toMatch(/cursor/i)
  })
})

describe('monitorLabel / filterChips', () => {
  it('labels each kind', () => {
    expect(monitorLabel(monitor()).text).toBe('#ops')
    expect(monitorLabel(monitor({ channel_label: 'ops' })).text).toBe('#ops')
    expect(monitorLabel(monitor({ kind: 'monitor_thread', thread_ts: '1.2' })).text).toBe('thread · #ops')
    expect(monitorLabel(monitor({ kind: 'monitor_thread', thread_ts: '1.2' })).title).toContain('1.2')
    expect(monitorLabel(monitor({ kind: 'monitor_mentions', channel: null })).text).toBe('mentions of you')
    expect(monitorLabel(monitor({ kind: 'monitor_dms', channel: null })).text).toBe('direct messages')
  })

  it('shows at most two chips and +n for the rest', () => {
    const m = monitor({ filters: { keywords: ['deploy', 'prod'], users: ['U0AAAAAA1'], user_labels: { U0AAAAAA1: 'alice' } } })
    const c = filterChips(m)
    expect(c.chips.map(x => x.text)).toEqual(['kw: deploy', 'kw: prod'])
    expect(c.more).toBe(1)
    expect(c.all[2]).toEqual({ text: '@alice', title: 'from alice (U0AAAAAA1)' })
    expect(filterChips(monitor()).chips).toEqual([])
    expect(filterChips(monitor({ filters: { users: ['U0AAAAAA1'], user_labels: {} } })).chips[0].text).toBe('@U0AAAAAA1')
  })
})

describe('times', () => {
  it('toEpoch accepts numbers, Slack ts strings and ISO', () => {
    expect(toEpoch(5)).toBe(5)
    expect(toEpoch('1800000000.000100')).toBeCloseTo(1800000000.0001)
    expect(toEpoch('2026-01-01T00:00:00+00:00')).toBe(Date.parse('2026-01-01T00:00:00Z') / 1000)
    expect(toEpoch(null)).toBeNull()
    expect(toEpoch('junk')).toBeNull()
  })

  it('relative wording is plain', () => {
    expect(agoText(NOW - 5, NOW)).toBe('just now')
    expect(agoText(NOW - 300, NOW)).toBe('5 min ago')
    expect(agoText(NOW - 7200, NOW)).toBe('2 h ago')
    expect(agoText(NOW - 3 * 86400, NOW)).toBe('3 d ago')
    expect(caughtUpTo(String(NOW - 300), NOW)).toBe('caught up to 5 min ago')
    expect(caughtUpTo(null, NOW)).toBe('')
    expect(expiresText(NOW + 2 * 86400, NOW)).toBe('expires in 2 d')
    expect(expiresText(NOW + 5 * 3600, NOW)).toBe('expires in 5 h')
    expect(expiresText(NOW - 1, NOW)).toBe('expired')
    expect(expiresText(null, NOW)).toBeNull()
    expect(deliveredText(monitor(), NOW)).toBe('delivered 5 min ago')
    expect(deliveredText(monitor({ last_delivered_at: null }), NOW)).toBe('nothing delivered yet')
  })
})

describe('health lines and attention', () => {
  it('monitorsLine and windowLine, with "at least" when truncated', () => {
    expect(monitorsLine(health())).toBe('2 active · 1 paused')
    expect(monitorsLine(health({ monitors: { total: 0 } }))).toBe('none')
    expect(windowLine(health())).toContain('delivered 41 msgs · dropped 3 · flood 0 · gaps 1 · caps 0 · instructions 5 ok / 1 refused')
    const w = health().window_24h
    expect(windowLine(health({ window_24h: { ...w, truncated: true } })).startsWith('at least ')).toBe(true)
  })

  it('every attention code has a label and hint; links carry the audit filter', () => {
    for (const code of Object.keys(ATTENTION_COPY)) {
      const item = { code, count: 1 }
      expect(attentionLabel(item)).toBeTruthy()
      expect(attentionHint(item)).toBeTruthy()
    }
    expect(attentionLabel({ code: 'monitors_flood', count: 3 })).toBe('flood ×3')
    expect(auditQuery({ audit: { events: ['monitor_flood', 'monitor_suspended'], session_id: 's1' } }))
      .toEqual({ tab: 'audit', events: 'monitor_flood,monitor_suspended', session: 's1' })
    expect(auditQuery({ audit: { events: ['inbound_connection'] } })).toEqual({ tab: 'audit', events: 'inbound_connection' })
  })

  it('only attention-level items count for the dot', () => {
    expect(hasAttention(health({ attention: [{ code: 'monitors_paused', severity: 'info' }] }))).toBe(false)
    expect(hasAttention(health({ attention: [{ code: 'auth_failed', severity: 'attention' }] }))).toBe(true)
    expect(hasAttention(null)).toBe(false)
  })

  it('isProblem ignores ok / delivered outcomes', () => {
    expect(isProblem({ outcome: 'ok' })).toBe(false)
    expect(isProblem({ outcome: 'delivered' })).toBe(false)
    expect(isProblem({})).toBe(false)
    for (const o of ['refused', 'late', 'rate_limited', 'error', 'dropped', 'delivery_failed']) expect(isProblem({ outcome: o })).toBe(true)
  })
})

describe('session summary helpers (3d-ii)', () => {
  const base = { handle: 'ea', policy_count: 1 }
  it('summaryMonitors tolerates an older Backend without the field', () => {
    expect(summaryMonitors(base)).toEqual({ active: 0, paused: 0, suspended: 0, scheduled: 0, total: 0 })
    expect(summaryMonitors(null).total).toBe(0)
    expect(summaryMonitors({ monitors: { active: 2, paused: 1, suspended: 1 } }).total).toBe(4)
  })
  it('summaryTitle', () => {
    expect(summaryTitle(null)).toBe('')
    expect(summaryTitle(base)).toBe('Relay attached as !ea · 1 channel policy')
    expect(summaryTitle({ ...base, monitors: { active: 3, paused: 0, suspended: 0 } })).toBe('Relay attached as !ea · 1 channel policy · 3 monitors')
    expect(summaryTitle({ ...base, monitors: { active: 1 }, attention: true })).toBe('Relay attached as !ea · 1 channel policy · 1 monitor · needs attention (monitors)')
  })
  it('monitorCountText', () => {
    expect(monitorCountText(1)).toBe('1 monitor')
    expect(monitorCountText(2)).toBe('2 monitors')
  })
})
