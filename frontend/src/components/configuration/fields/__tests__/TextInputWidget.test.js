import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TextInputWidget from '../TextInputWidget.vue'
import { FIELD_SCHEMAS } from '../fieldSchemas.js'

describe('TextInputWidget numeric mode (issue #2287)', () => {
  it('emits an integer, or null when cleared/invalid', async () => {
    const w = mount(TextInputWidget, { props: { value: null, numeric: true } })
    const input = w.find('input')
    expect(input.attributes('type')).toBe('number')
    await input.setValue('150000')
    await input.setValue('')
    await input.setValue('0')
    expect(w.emitted('update:value').map((e) => e[0])).toEqual([150000, null, null])
  })

  it('stays a string input by default', async () => {
    const w = mount(TextInputWidget, { props: { value: '' } })
    await w.find('input').setValue('abc')
    expect(w.find('input').attributes('type')).toBe('text')
    expect(w.emitted('update:value')[0]).toEqual(['abc'])
  })
})

describe('TextInputWidget ms hint (issue #2287)', () => {
  const hint = (value) =>
    mount(TextInputWidget, { props: { value, numeric: true, unit: 'ms' } }).find('.form-text').text()

  it('formats durations', () => {
    expect(hint(500)).toBe('= 500 ms')
    expect(hint(60000)).toBe('= 1 min')
    expect(hint(45000)).toBe('= 45 s')
    expect(hint(10800000)).toBe('= 3 h')
  })

  it('shows no hint when unset or unitless', () => {
    expect(mount(TextInputWidget, { props: { value: null, numeric: true, unit: 'ms' } }).find('.form-text').exists()).toBe(false)
    expect(mount(TextInputWidget, { props: { value: 5, numeric: true } }).find('.form-text').exists()).toBe(false)
  })
})

describe('retry watchdog schema', () => {
  const fields = Object.values(FIELD_SCHEMAS).flat()
  const get = (k) => fields.find((f) => f.key === k)

  it('shows watchdog tunables only when the master switch is on', () => {
    for (const k of ['retry_watchdog_max_wait_ms', 'retry_watchdog_max_usage_limit_wait_ms', 'retry_watchdog_usage_limit_recheck_ms']) {
      expect(get(k).showWhen({ retry_watchdog: true })).toBe(true)
      expect(get(k).showWhen({ retry_watchdog: null })).toBe(false)
      expect(get(k).showWhen({ retry_watchdog: false })).toBe(false)
    }
  })

  it('overloaded-retry delays are independent of the watchdog', () => {
    expect(get('overloaded_retry_base_delay_ms').showWhen).toBeUndefined()
    expect(get('overloaded_retry_max_delay_ms').showWhen).toBeUndefined()
  })
})

describe('idle compaction schema', () => {
  const fields = Object.values(FIELD_SCHEMAS).flat()
  const get = (k) => fields.find((f) => f.key === k)

  it('describes the kill-switch semantics', () => {
    expect(get('disable_idle_compaction').description).toMatch(/Kill switch only/)
    expect(get('disable_idle_compaction').description).toMatch(/cannot be forced on/)
  })

  it('hides min-tokens when idle compaction is disabled', () => {
    const f = get('idle_compact_min_tokens')
    expect(f.numeric).toBe(true)
    expect(f.showWhen({ disable_idle_compaction: true })).toBe(false)
    expect(f.showWhen({ disable_idle_compaction: null })).toBe(true)
  })
})
