import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import router from '@/router'
import { useSettingsStore } from '@/stores/settings'

describe('router dirty guard', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    await router.push('/settings/relays')
  })

  it('lets a query-only change on the same path through while dirty (Relays tabs)', async () => {
    const settings = useSettingsStore()
    settings.setField('relay:r1:inbound', 'dirty', true)
    await router.push({ path: '/settings/relays', query: { tab: 'inbound' } })
    expect(router.currentRoute.value.query.tab).toBe('inbound')
    expect(settings.pendingNavigation).toBeNull()
  })

  it('still blocks leaving the section while dirty', async () => {
    const settings = useSettingsStore()
    settings.setField('relay:r1:inbound', 'dirty', true)
    await router.push('/settings/secrets')
    expect(router.currentRoute.value.path).toBe('/settings/relays')
    expect(settings.pendingNavigation).not.toBeNull()
  })
})
