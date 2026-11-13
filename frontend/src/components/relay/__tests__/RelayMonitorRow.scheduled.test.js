import { describe, it, expect } from 'vitest'
import { renderWithStores } from '@/test-utils/render'
import RelayMonitorRow from '@/components/relay/RelayMonitorRow.vue'
import { NOW, digest, monitor, unreadMonitor } from './monitorFixtures'

const mountRow = (m, props = {}) => renderWithStores(RelayMonitorRow, { props: { monitor: m, nowSec: NOW, ...props } })
const t = (w, id) => w.find(`[data-testid="${id}"]`)

describe('scheduled rows', () => {
  it('digest row: clock icon, label, schedule, next and last run on the muted line', () => {
    const { wrapper } = mountRow(digest())
    expect(t(wrapper, 'monitor-icon').attributes('data-icon')).toBe('clock')
    expect(t(wrapper, 'monitor-icon').find('svg').exists()).toBe(true)
    expect(t(wrapper, 'monitor-label').text()).toBe('digest · #ops')
    expect(t(wrapper, 'sched-schedule').text()).toBe('daily 09:00, 17:00 Mon–Fri')
    expect(t(wrapper, 'sched-next').text()).toBe('· next run in 3 h')
    expect(t(wrapper, 'sched-last').text()).toBe('· last: delivered 12 messages · 2 h ago')
    expect(t(wrapper, 'monitor-sched').attributes('title')).toContain('Europe/Paris (your Slack timezone)')
  })

  it('unread row: inbox icon and option chips', () => {
    const { wrapper } = mountRow(unreadMonitor())
    expect(t(wrapper, 'monitor-icon').attributes('data-icon')).toBe('inbox')
    expect(t(wrapper, 'monitor-label').text()).toBe('unread')
    expect(wrapper.findAll('[data-testid="monitor-chips"] .relay-tag').map(c => c.text())).toEqual(['DMs', '#alpha', '+1'])
    expect(t(wrapper, 'sched-schedule').text()).toBe('every 2 h')
  })

  it('live rows keep the text glyph and show no schedule line or Run now', () => {
    const { wrapper } = mountRow(monitor())
    expect(t(wrapper, 'monitor-icon').exists()).toBe(false)
    expect(t(wrapper, 'monitor-sched').exists()).toBe(false)
    expect(t(wrapper, 'monitor-run').exists()).toBe(false)
  })

  it('older Backend without mode renders as a live row', () => {
    const { wrapper } = mountRow(monitor({ mode: undefined }))
    expect(t(wrapper, 'monitor-run').exists()).toBe(false)
  })

  it('shows overdue, not-run-yet and the running state', () => {
    const overdue = mountRow(digest({ next_run: NOW - 900, last_run: null })).wrapper
    expect(t(overdue, 'sched-next').text()).toBe('· overdue by 15 min')
    expect(t(overdue, 'sched-last').text()).toBe('· not run yet')
    const running = mountRow(digest(), { running: true }).wrapper
    expect(t(running, 'sched-running').text()).toBe('· Running…')
    expect(t(running, 'monitor-run').attributes('disabled')).toBeDefined()
  })

  it('hides the next run on a paused or suspended row', () => {
    expect(t(mountRow(digest({ paused: true, effective_state: 'paused' })).wrapper, 'sched-next').exists()).toBe(false)
    expect(t(mountRow(digest({ status: 'suspended', suspended_reason: 'flood', effective_state: 'suspended' })).wrapper, 'sched-next').exists()).toBe(false)
  })
})

describe('Run now', () => {
  it('icon button with the frozen title; emits run once per click', async () => {
    const { wrapper } = mountRow(digest())
    const btn = t(wrapper, 'monitor-run')
    expect(btn.attributes('title')).toBe('Run now: collects and delivers immediately; wakes the session only if there is content. Applies immediately.')
    expect(btn.find('svg').exists()).toBe(true)
    await btn.trigger('click')
    expect(wrapper.emitted('run')[0][0].id).toBe('d1')
  })

  it('is disabled with the reason when paused or suspended', () => {
    const paused = mountRow(digest({ paused: true, effective_state: 'paused' })).wrapper
    expect(t(paused, 'monitor-run').attributes('disabled')).toBeDefined()
    expect(t(paused, 'monitor-run').attributes('title')).toBe('Paused: resume it first.')
    const sus = mountRow(digest({ status: 'suspended', suspended_reason: 'channel_blocked', effective_state: 'suspended' })).wrapper
    expect(t(sus, 'monitor-run').attributes('disabled')).toBeDefined()
    expect(t(sus, 'monitor-run').attributes('title')).toContain('channel blocked')
  })

  it('shows the note and the error lines', () => {
    const { wrapper } = mountRow(digest(), { note: 'Started: the result appears in "last run".', error: 'Please wait 30 s before running it again.' })
    expect(t(wrapper, 'monitor-note').text()).toContain('Started')
    expect(t(wrapper, 'monitor-error').text()).toContain('wait 30 s')
  })

  it('delete confirm explains the schedule consequence', async () => {
    const { wrapper } = mountRow(digest())
    await t(wrapper, 'monitor-delete').trigger('click')
    expect(t(wrapper, 'monitor-confirm').text()).toContain('Stops the schedule and removes queued content')
  })
})

describe('expanded detail', () => {
  it('digest: schedule, window, last run, failures', async () => {
    const { wrapper } = mountRow(digest({ run_failures: 2, last_run: { at: NOW - 600, outcome: 'delivered', messages: 4, gap: true, trigger: 'manual' } }))
    await t(wrapper, 'monitor-expand').trigger('click')
    expect(t(wrapper, 'detail-schedule').text()).toContain('daily at 09:00 and 17:00, Mon–Fri, Europe/Paris (your Slack timezone)')
    expect(t(wrapper, 'detail-tz').text()).toContain('set when the monitor was created')
    expect(t(wrapper, 'detail-window').text()).toContain('at most 72 h')
    expect(t(wrapper, 'detail-window').text()).toContain('2 min ago')
    expect(t(wrapper, 'detail-last-run').text()).toContain('run now (you)')
    expect(t(wrapper, 'detail-gap').text()).toContain('Incomplete')
    expect(t(wrapper, 'detail-run-failures').text()).toContain('2 failed runs in a row')
    expect(t(wrapper, 'detail-repeat').exists()).toBe(false)
  })

  it('says UTC is used when Slack did not report a timezone', async () => {
    const { wrapper } = mountRow(digest({ tz: 'UTC', tz_source: 'utc_fallback' }))
    await t(wrapper, 'monitor-expand').trigger('click')
    expect(t(wrapper, 'detail-tz-fallback').text()).toBe('Slack did not report your timezone, so UTC is used.')
  })

  it('unread: options in words and no 72 h window', async () => {
    const { wrapper } = mountRow(unreadMonitor())
    await t(wrapper, 'monitor-expand').trigger('click')
    expect(t(wrapper, 'detail-unread').text()).toContain('except 2, channels #alpha, only messages that mention you')
    expect(t(wrapper, 'detail-window').text()).toContain("Slack's own unread state")
    expect(t(wrapper, 'detail-window').text()).not.toContain('72 h')
  })

  it('repeat notice only when a live monitor of the same scope is loaded', async () => {
    const twin = monitor({ id: 'live1' })
    const { wrapper } = mountRow(digest(), { peers: [digest(), twin] })
    await t(wrapper, 'monitor-expand').trigger('click')
    expect(t(wrapper, 'detail-repeat').text()).toContain('twice')
    const other = mountRow(digest(), { peers: [digest(), monitor({ id: 'live2', channel: 'C0OTHER001' })] }).wrapper
    await t(other, 'monitor-expand').trigger('click')
    expect(t(other, 'detail-repeat').exists()).toBe(false)
  })

  it('renders hostile labels as text', async () => {
    const evil = '<img src=x onerror=alert(1)>'
    const { wrapper } = mountRow(unreadMonitor({
      unread: { dms: true, channels: ['C0AAAAAAA2'], exclude: [], mentions_only: false, repeat: false, labels: { C0AAAAAAA2: evil } },
      unread_text: `channels ${evil}`,
    }))
    await t(wrapper, 'monitor-expand').trigger('click')
    expect(wrapper.find('img').exists()).toBe(false)
    expect(t(wrapper, 'detail-unread').text()).toContain('<img src=x')
  })
})

describe('theme', () => {
  it('new row styles use theme variables only', async () => {
    const src = (await import('../RelayMonitorRow.vue?raw')).default
    const css = src.slice(src.indexOf('<style'))
    const literals = css.match(/#[0-9a-fA-F]{3,6}\b(?![^()]*\))/g) || []
    expect(literals).toEqual([])
  })
})
