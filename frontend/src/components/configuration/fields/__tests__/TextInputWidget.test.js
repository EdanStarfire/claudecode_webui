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
