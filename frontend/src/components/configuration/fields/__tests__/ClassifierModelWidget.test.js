import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ClassifierModelWidget from '../ClassifierModelWidget.vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'
import { CONFIG_FIELDS_LIST, fieldArea } from '@/utils/configFields'

describe('ClassifierModelWidget', () => {
  beforeEach(() => setActivePinia(createPinia()))

  function mountWidget(props = {}) {
    const store = useProviderCatalogStore()
    store.entries = [{ id: 'cat', name: 'Cat', models: [{ id: 'm1', litellm_model: 'openai/x' }] }]
    store.loaded = true
    return mount(ClassifierModelWidget, { props })
  }

  it('offers catalog models as catalog--model aliases', () => {
    const w = mountWidget()
    expect(w.find('datalist option').attributes('value')).toBe('cat--m1')
  })

  it('emits typed value, null when cleared', async () => {
    const w = mountWidget()
    await w.find('input').setValue('cat--m1')
    expect(w.emitted('update:value')[0]).toEqual(['cat--m1'])
    await w.find('input').setValue('')
    expect(w.emitted('update:value')[1]).toEqual([null])
  })

  it('shows hint for catalog alias', () => {
    expect(mountWidget({ value: 'cat--m1' }).text()).toContain('LiteLLM catalog model')
  })

  it('is registered in the config tables', () => {
    expect(CONFIG_FIELDS_LIST).toContain('auto_mode_classifier_model')
    expect(fieldArea('auto_mode_classifier_model')).toBe('permissions')
  })
})
