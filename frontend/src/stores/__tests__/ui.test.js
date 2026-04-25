import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('ui store', () => {
  it('toggleRightSidebar flips rightSidebarCollapsed and persists to localStorage', async () => {
    const { useUIStore } = await import('@/stores/ui')
    const store = useUIStore()

    const initial = store.rightSidebarCollapsed
    store.toggleRightSidebar()

    expect(store.rightSidebarCollapsed).toBe(!initial)
    expect(localStorage.getItem('webui-sidebar-rightCollapsed')).toBe(JSON.stringify(!initial))
  })

  it('showModal sets activeModal after setTimeout; hideModal clears it', async () => {
    vi.useFakeTimers()
    const { useUIStore } = await import('@/stores/ui')
    const store = useUIStore()

    store.showModal('create-project', { foo: 1 })

    // Before timeout fires, modal is cleared
    expect(store.activeModal).toBeNull()

    vi.advanceTimersByTime(0)

    expect(store.activeModal).toBe('create-project')
    expect(store.modalData).toEqual({ foo: 1 })

    store.hideModal()

    expect(store.activeModal).toBeNull()
    expect(store.modalData).toBeNull()
  })

  it('handleResize updates windowWidth and isMobile computed', async () => {
    const { useUIStore } = await import('@/stores/ui')
    const store = useUIStore()

    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 500 })
    store.handleResize()
    expect(store.isMobile).toBe(true)

    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1200 })
    store.handleResize()
    expect(store.isMobile).toBe(false)
  })
})
