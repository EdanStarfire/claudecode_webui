import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('settings store', () => {
  async function getStore() {
    const { useSettingsStore } = await import('@/stores/settings')
    return useSettingsStore()
  }

  it('starts with no dirty areas and empty drafts', async () => {
    const store = await getStore()
    expect(store.hasDirtyAreas).toBe(false)
    expect(store.dirtyAreas.size).toBe(0)
    expect(store.drafts.size).toBe(0)
  })

  it('setField creates draft and marks area dirty', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'My Session')
    expect(store.hasDirtyAreas).toBe(true)
    expect(store.dirtyAreas.has('session:abc:general')).toBe(true)
    expect(store.getDraft('session:abc:general').name).toBe('My Session')
  })

  it('setField accumulates multiple fields in same area', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'A')
    store.setField('session:abc:general', 'model', 'claude-3')
    const draft = store.getDraft('session:abc:general')
    expect(draft.name).toBe('A')
    expect(draft.model).toBe('claude-3')
  })

  it('markClean removes area from dirtyAreas and drafts', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    store.markClean('session:abc:general')
    expect(store.hasDirtyAreas).toBe(false)
    expect(store.dirtyAreas.has('session:abc:general')).toBe(false)
    expect(store.drafts.has('session:abc:general')).toBe(false)
  })

  it('discardDraft behaves same as markClean', async () => {
    const store = await getStore()
    store.setField('template:t1:model-tuning', 'model', 'claude-opus')
    store.discardDraft('template:t1:model-tuning')
    expect(store.hasDirtyAreas).toBe(false)
  })

  it('getDraft initialises empty object for new areaKey', async () => {
    const store = await getStore()
    const draft = store.getDraft('app:features')
    expect(draft).toEqual({})
    expect(store.hasDirtyAreas).toBe(false)
  })

  it('requestNavigation returns false when no dirty areas', async () => {
    const store = await getStore()
    const blocked = store.requestNavigation({ path: '/session/x' })
    expect(blocked).toBe(false)
    expect(store.pendingNavigation).toBeNull()
  })

  it('requestNavigation returns true and stores route when dirty', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    const route = { path: '/settings/features' }
    const blocked = store.requestNavigation(route)
    expect(blocked).toBe(true)
    expect(store.pendingNavigation).toStrictEqual(route)
  })

  it('confirmNavigation cancel clears pending but keeps dirty', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    store.requestNavigation({ path: '/other' })
    store.confirmNavigation('cancel')
    expect(store.pendingNavigation).toBeNull()
    expect(store.hasDirtyAreas).toBe(true)
  })

  it('confirmNavigation discard clears all dirty areas and returns route', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    store.setField('session:abc:model', 'model', 'y')
    const route = { path: '/settings/features' }
    store.requestNavigation(route)
    const returned = store.confirmNavigation('discard')
    expect(returned).toStrictEqual(route)
    expect(store.hasDirtyAreas).toBe(false)
    expect(store.pendingNavigation).toBeNull()
  })

  it('confirmNavigation apply returns route without discarding drafts', async () => {
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    const route = { path: '/settings/features' }
    store.requestNavigation(route)
    const returned = store.confirmNavigation('apply')
    expect(returned).toStrictEqual(route)
    // 'apply' does NOT auto-discard — caller saves first then calls markClean
    expect(store.hasDirtyAreas).toBe(true)
    expect(store.pendingNavigation).toBeNull()
  })

  it('registers beforeunload when dirty areas exist', async () => {
    const addSpy = vi.spyOn(window, 'addEventListener')
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    // Watcher is async; flush via nextTick
    await new Promise(r => setTimeout(r, 0))
    expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))
  })

  it('removes beforeunload when no dirty areas remain', async () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    const store = await getStore()
    store.setField('session:abc:general', 'name', 'X')
    await new Promise(r => setTimeout(r, 0))
    store.markClean('session:abc:general')
    await new Promise(r => setTimeout(r, 0))
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))
  })
})
