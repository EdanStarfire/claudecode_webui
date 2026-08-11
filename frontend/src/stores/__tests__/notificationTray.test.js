import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('notificationTray store', () => {
  it('addEntry prepends a new entry with title/body derived from the event type', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('task_complete', { sessionName: 'My Session', sessionId: 'sess-1' })

    expect(store.entries.length).toBe(1)
    expect(store.entries[0]).toMatchObject({
      eventType: 'task_complete',
      title: 'Task Complete',
      body: 'My Session has completed processing',
      sessionId: 'sess-1',
      sessionName: 'My Session',
    })
    expect(store.entries[0].id).toBeTruthy()
    expect(typeof store.entries[0].timestamp).toBe('number')
  })

  it('addEntry unshifts newest entries to the front', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('task_complete', { sessionName: 'First' })
    store.addEntry('session_error', { sessionName: 'Second' })

    expect(store.entries.map(e => e.sessionName)).toEqual(['Second', 'First'])
  })

  it('caps entries at 100, dropping the oldest', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    for (let i = 0; i < 105; i++) {
      store.addEntry('task_complete', { sessionName: `session-${i}` })
    }

    expect(store.entries.length).toBe(100)
    // Newest (session-104) at front, oldest 5 (session-0..4) dropped
    expect(store.entries[0].sessionName).toBe('session-104')
    expect(store.entries.some(e => e.sessionName === 'session-4')).toBe(false)
    expect(store.entries.some(e => e.sessionName === 'session-5')).toBe(true)
  })

  it('dismissEntry removes a single entry by id', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('task_complete', { sessionName: 'A' })
    store.addEntry('session_error', { sessionName: 'B' })
    const idToRemove = store.entries.find(e => e.sessionName === 'A').id

    store.dismissEntry(idToRemove)

    expect(store.entries.length).toBe(1)
    expect(store.entries[0].sessionName).toBe('B')
  })

  it('clearAll empties the tray', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('task_complete', { sessionName: 'A' })
    store.addEntry('session_error', { sessionName: 'B' })

    store.clearAll()

    expect(store.entries).toEqual([])
  })

  it('markReadAndNavigate removes the entry and returns its sessionId', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('minion_comm', { fromMinion: 'Bob', sessionId: 'minion-42' })
    const id = store.entries[0].id

    const sessionId = store.markReadAndNavigate(id)

    expect(sessionId).toBe('minion-42')
    expect(store.entries).toEqual([])
  })

  it('markReadAndNavigate returns null when the entry has no sessionId', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('task_complete', { sessionName: 'No Session' })
    const id = store.entries[0].id

    const sessionId = store.markReadAndNavigate(id)

    expect(sessionId).toBeNull()
  })

  it('persists to localStorage and hydrates a fresh store instance from it', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    store.addEntry('task_complete', { sessionName: 'Persisted', sessionId: 'sess-9' })

    const raw = localStorage.getItem('webui-notification-tray')
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw)).toHaveLength(1)

    // Simulate a fresh page load: new Pinia instance, module re-hydrates from localStorage
    setActivePinia(createPinia())
    const store2 = useNotificationTrayStore()

    expect(store2.entries.length).toBe(1)
    expect(store2.entries[0].sessionName).toBe('Persisted')
  })

  it('unreadCount reflects the number of entries', async () => {
    const { useNotificationTrayStore } = await import('@/stores/notificationTray')
    const store = useNotificationTrayStore()

    expect(store.unreadCount).toBe(0)
    store.addEntry('task_complete', {})
    store.addEntry('session_error', {})
    expect(store.unreadCount).toBe(2)
  })
})
