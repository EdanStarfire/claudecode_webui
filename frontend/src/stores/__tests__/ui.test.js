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

  describe('flatGroupMode migration (issue #1722)', () => {
    // flatGroupMode is read from localStorage at module load time, so each case needs
    // a fresh module instance (vi.resetModules) with localStorage seeded beforehand.
    it('migrates legacy groupByState=true to flatGroupMode="status"', async () => {
      localStorage.setItem('webui-sidebar-groupByState', JSON.stringify(true))
      vi.resetModules()
      const { useUIStore } = await import('@/stores/ui')
      const store = useUIStore()

      expect(store.flatGroupMode).toBe('status')
      expect(localStorage.getItem('webui-sidebar-flatGroupMode')).toBe(JSON.stringify('status'))
    })

    it('migrates legacy groupByState=false to flatGroupMode="none"', async () => {
      localStorage.setItem('webui-sidebar-groupByState', JSON.stringify(false))
      vi.resetModules()
      const { useUIStore } = await import('@/stores/ui')
      const store = useUIStore()

      expect(store.flatGroupMode).toBe('none')
    })

    it('defaults to "none" when no legacy key or new key is present', async () => {
      vi.resetModules()
      const { useUIStore } = await import('@/stores/ui')
      const store = useUIStore()

      expect(store.flatGroupMode).toBe('none')
    })

    it('prefers an existing flatGroupMode key over the legacy boolean', async () => {
      localStorage.setItem('webui-sidebar-groupByState', JSON.stringify(true))
      localStorage.setItem('webui-sidebar-flatGroupMode', JSON.stringify('custom'))
      vi.resetModules()
      const { useUIStore } = await import('@/stores/ui')
      const store = useUIStore()

      expect(store.flatGroupMode).toBe('custom')
    })

    it('setFlatGroupMode only accepts none/status/custom and persists valid values', async () => {
      vi.resetModules()
      const { useUIStore } = await import('@/stores/ui')
      const store = useUIStore()

      store.setFlatGroupMode('custom')
      expect(store.flatGroupMode).toBe('custom')

      store.setFlatGroupMode('bogus')
      expect(store.flatGroupMode).toBe('custom')
    })
  })

  describe('setAgentSort (issue #1722)', () => {
    it('accepts last_active in addition to alpha/creation', async () => {
      const { useUIStore } = await import('@/stores/ui')
      const store = useUIStore()

      store.setAgentSort('last_active')
      expect(store.agentSort).toBe('last_active')

      store.setAgentSort('bogus')
      expect(store.agentSort).toBe('last_active')
    })
  })
})
