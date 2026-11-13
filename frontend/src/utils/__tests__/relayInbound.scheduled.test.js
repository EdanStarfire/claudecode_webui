import { describe, it, expect } from 'vitest'
import {
  ATTENTION_COPY, digestWindowLine, filterChips, hasLiveTwin, isProblem, lastRunText, monitorLabel, monitorsLine,
  nextRunText, runNowDisabledReason, runNowError, scheduleSummary, summaryTitle, triggerWord, unreadChips,
} from '@/utils/relayInbound'
import { NOW, digest, monitor, unreadMonitor } from '@/components/relay/__tests__/monitorFixtures'

describe('scheduled monitor labels', () => {
  it.each([
    [{ kind: 'monitor_channel' }, 'digest · #ops', 'clock'],
    [{ kind: 'monitor_thread', thread_ts: '1.2' }, 'digest · thread · #ops', 'clock'],
    [{ kind: 'monitor_mentions', channel: null }, 'digest · mentions of you', 'clock'],
    [{ kind: 'monitor_dms', channel: null }, 'digest · direct messages', 'clock'],
  ])('digest %j', (over, text, icon) => {
    const l = monitorLabel(digest(over))
    expect(l.text).toBe(text)
    expect(l.icon).toBe(icon)
    expect(l.glyph).toBe('')
  })
  it('unread and live', () => {
    expect(monitorLabel(unreadMonitor())).toMatchObject({ text: 'unread', icon: 'inbox' })
    expect(monitorLabel(monitor())).toMatchObject({ text: '#ops', icon: '', glyph: '#' })
  })
  it('unread option chips', () => {
    expect(unreadChips(unreadMonitor()).map(c => c.text)).toEqual(['DMs', '#alpha', '@mentions only'])
    expect(unreadChips(unreadMonitor({ unread: { dms: false, channels: [], exclude: [], mentions_only: false, repeat: true, labels: {} } })).map(c => c.text)).toEqual(['repeat'])
    expect(filterChips(unreadMonitor()).chips).toHaveLength(2)
  })
})

describe('scheduleSummary / nextRunText', () => {
  it('short forms', () => {
    expect(scheduleSummary(digest())).toBe('daily 09:00, 17:00 Mon–Fri')
    expect(scheduleSummary(digest({ schedule: { type: 'daily', times: ['08:30'], days: [0, 1, 2, 3, 4, 5, 6], tz: 'UTC', tz_source: 'utc_fallback' } }))).toBe('daily 08:30')
    expect(scheduleSummary(digest({ schedule: { type: 'daily', times: ['08:30'], days: [0, 2], tz: 'UTC', tz_source: 'explicit' } }))).toBe('daily 08:30 Mon, Wed')
    expect(scheduleSummary(unreadMonitor())).toBe('every 2 h')
    expect(scheduleSummary({ schedule: { type: 'interval', minutes: 90 } })).toBe('every 90 min')
    expect(scheduleSummary({ schedule: null, schedule_text: 'x' })).toBe('x')
  })
  it('relative next run and overdue', () => {
    expect(nextRunText(NOW + 30, NOW)).toBe('next run in moments')
    expect(nextRunText(NOW + 12 * 60, NOW)).toBe('next run in 12 min')
    expect(nextRunText(NOW + 3 * 3600 + 120, NOW)).toBe('next run in 3 h')
    expect(nextRunText(NOW - 12 * 60, NOW)).toBe('overdue by 12 min')
    expect(nextRunText(NOW - 20, NOW)).toBe('next run in moments')
    expect(nextRunText(null, NOW)).toBe('')
    expect(nextRunText(NOW + 30 * 3600, NOW)).toMatch(/^next run (tomorrow|[A-Z][a-z]{2}) \d\d:\d\d$/)
  })
})

describe('lastRunText', () => {
  const at = NOW - 7200
  it('every outcome in plain words', () => {
    expect(lastRunText(null, NOW)).toBe('')
    expect(lastRunText({ at, outcome: 'delivered', messages: 12 }, NOW)).toBe('delivered 12 messages · 2 h ago')
    expect(lastRunText({ at, outcome: 'delivered', messages: 1, omitted: 3 }, NOW)).toBe('delivered 1 message (+3 omitted) · 2 h ago')
    expect(lastRunText({ at, outcome: 'delivered', messages: 2, late: true }, NOW, { unread: true })).toBe('ran late after a restart · delivered 2 conversations · 2 h ago')
    expect(lastRunText({ at, outcome: 'empty' }, NOW)).toBe('empty · 2 h ago (nothing new, session not woken)')
    expect(lastRunText({ at, outcome: 'rate_limited', code: 'rate_limited_rescheduled', retry: 2, retry_at: NOW + 300 }, NOW)).toMatch(/^rate limited · retrying \d\d:\d\d( [AP]M)? \(2\/3\)$/)
    expect(lastRunText({ at, outcome: 'rate_limited', code: 'rate_limited_gave_up', retry: 3 }, NOW)).toBe('gave up this slot after 3 tries · 2 h ago')
    expect(lastRunText({ at, outcome: 'rate_limited' }, NOW)).toBe('rate limited · 2 h ago')
    expect(lastRunText({ at, outcome: 'deferred', retry_at: NOW + 600 }, NOW)).toMatch(/^deferred \(session busy\) · retrying \d\d:\d\d( [AP]M)?$/)
    expect(lastRunText({ at, outcome: 'deferred' }, NOW)).toBe('deferred (session busy)')
    expect(lastRunText({ at, outcome: 'refused', code: 'channel_blocked' }, NOW)).toBe('refused: channel blocked · 2 h ago')
    expect(lastRunText({ at, outcome: 'failed' }, NOW)).toBe('failed · 2 h ago')
  })
  it('trigger words', () => {
    expect([triggerWord('schedule'), triggerWord('late'), triggerWord('manual'), triggerWord('agent')]).toEqual(
      ['schedule', 'late (missed slot)', 'run now (you)', 'run now (agent)'])
  })
})

describe('run now helpers', () => {
  it('error sentences', () => {
    expect(runNowError({ status: 429, code: 'run_too_soon', retryAfter: 41.2 })).toBe('Please wait 42 s before running it again.')
    expect(runNowError({ status: 409, code: 'monitor_paused' })).toContain('resume it first')
    expect(runNowError({ status: 409, code: 'monitor_suspended' })).toContain('Suspended')
    expect(runNowError({ status: 409, code: 'run_in_progress' })).toContain('already in progress')
    expect(runNowError({ status: 409, code: 'inbound_unavailable' })).toContain('switched off')
    expect(runNowError({ status: 404 })).toContain('already removed')
    expect(runNowError({ status: 500 })).toContain('failed')
  })
  it('disabled reasons', () => {
    expect(runNowDisabledReason(digest())).toBe('')
    expect(runNowDisabledReason(digest({ paused: true }))).toContain('Paused')
    expect(runNowDisabledReason(digest({ status: 'suspended', suspended_reason: 'channel_blocked' }))).toContain('channel blocked')
  })
  it('repeat twin: live monitor of the same session and scope', () => {
    const d = digest()
    expect(hasLiveTwin(d, [d, monitor({ id: 'l' })])).toBe(true)
    expect(hasLiveTwin(d, [d, monitor({ id: 'l', session_id: 's2' })])).toBe(false)
    expect(hasLiveTwin(d, [d, monitor({ id: 'l', channel: 'C0OTHER001' })])).toBe(false)
    expect(hasLiveTwin(d, [d, digest({ id: 'd2' })])).toBe(false)
    expect(hasLiveTwin(monitor(), [monitor({ id: 'l' })])).toBe(false)
  })
})

describe('health lines and copy', () => {
  it('monitorsLine adds scheduled with the next run', () => {
    expect(monitorsLine({ monitors: { total: 3, active: 2, paused: 1, suspended: 0, scheduled: 3, next_run: NOW + 12 * 60 } }, NOW))
      .toBe('2 active · 1 paused · 3 scheduled (next run in 12 min)')
    expect(monitorsLine({ monitors: { total: 1, active: 1, scheduled: 1, next_run: null } }, NOW)).toBe('1 active · 1 scheduled')
  })
  it('digestWindowLine only when something ran', () => {
    expect(digestWindowLine({ window_24h: { digest_delivered: 0, digest_empty: 0, rate_limited_runs: 0, late_runs: 0 } })).toBe('')
    expect(digestWindowLine({ window_24h: { digest_delivered: 5, digest_empty: 2, rate_limited_runs: 1, late_runs: 1 } }))
      .toBe('digests 5 delivered · 2 empty · rate limited ×1 · ran late ×1')
  })
  it('copy exists for every new code', () => {
    for (const c of ['digest_failures', 'digest_rate_limited_24h', 'digest_overdue', 'digest_ran_late', 'digest_deferred']) {
      expect(ATTENTION_COPY[c]).toHaveLength(2)
    }
  })
  it('empty / accepted digest rows are not problems; deferred / refused are', () => {
    expect([{ outcome: 'empty' }, { outcome: 'accepted' }, { outcome: 'delivered' }].some(isProblem)).toBe(false)
    expect(['deferred', 'refused', 'failed', 'rate_limited'].every(o => isProblem({ outcome: o }))).toBe(true)
  })
  it('chip title names scheduled monitors only when all are scheduled', () => {
    const s = { handle: 'a', policy_count: 0, attention: true }
    expect(summaryTitle({ ...s, monitors: { active: 2, scheduled: 2 } })).toContain('scheduled monitors')
    expect(summaryTitle({ ...s, monitors: { active: 2, scheduled: 1 } })).not.toContain('scheduled')
  })
})
