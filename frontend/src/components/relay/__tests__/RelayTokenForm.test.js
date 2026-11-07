import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RelayTokenForm from '@/components/relay/RelayTokenForm.vue'

describe('RelayTokenForm', () => {
  it('uses password inputs with autocomplete off', () => {
    const w = mount(RelayTokenForm)
    for (const id of ['relay-user-token', 'relay-app-token']) {
      const el = w.find(`[data-testid="${id}"]`)
      expect(el.attributes('type')).toBe('password')
      expect(el.attributes('autocomplete')).toBe('off')
    }
  })

  it('hints on a wrong prefix and blocks submit', async () => {
    const w = mount(RelayTokenForm)
    await w.find('[data-testid="relay-user-token"]').setValue('xoxb-nope')
    expect(w.find('[data-testid="relay-user-hint"]').text()).toContain('xoxp-')
    await w.find('form').trigger('submit')
    expect(w.emitted('submit')).toBeUndefined()
  })

  it('emits once and clears both inputs immediately', async () => {
    const w = mount(RelayTokenForm)
    const user = w.find('[data-testid="relay-user-token"]')
    await user.setValue('xoxp-abc')
    await w.find('[data-testid="relay-app-token"]').setValue('xapp-def')
    await w.find('form').trigger('submit')
    expect(w.emitted('submit')[0][0]).toEqual({ userToken: 'xoxp-abc', appToken: 'xapp-def' })
    expect(user.element.value).toBe('')
    expect(w.find('[data-testid="relay-app-token"]').element.value).toBe('')
    expect(w.text()).not.toContain('xoxp-abc')
  })

  it('accepts either token alone and renders a backend error as text', async () => {
    const w = mount(RelayTokenForm, { props: { error: 'Invalid token <b>x</b>' } })
    await w.find('[data-testid="relay-app-token"]').setValue('xapp-1')
    await w.find('form').trigger('submit')
    expect(w.emitted('submit')[0][0]).toEqual({ userToken: '', appToken: 'xapp-1' })
    expect(w.find('[data-testid="relay-token-error"]').text()).toBe('Invalid token <b>x</b>')
    expect(w.find('[data-testid="relay-token-error"] b').exists()).toBe(false)
  })
})
