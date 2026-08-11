import { describe, it, expect, beforeEach, vi } from 'vitest'

const addEntryMock = vi.fn()
vi.mock('@/stores/notificationTray', () => ({
  useNotificationTrayStore: () => ({ addEntry: addEntryMock })
}))

async function flushMicrotasks() {
  await new Promise(resolve => setTimeout(resolve, 0))
}

beforeEach(() => {
  vi.resetModules()
  addEntryMock.mockReset()
})

describe('useNotifications — tray settings (#1725)', () => {
  it('getSettings() defaults trayEnabled to true, unlike sound/native which default false', async () => {
    const { getSettings } = await import('@/composables/useNotifications')
    const settings = getSettings()

    expect(settings.trayEnabled).toBe(true)
    expect(settings.soundEnabled).toBe(false)
    expect(settings.nativeEnabled).toBe(false)
  })

  it('getSettings() defaults trayEvents to mirror nativeEvents (all true except minion_comm)', async () => {
    const { getSettings } = await import('@/composables/useNotifications')
    const settings = getSettings()

    expect(settings.trayEvents).toEqual({
      permission_prompt: true,
      task_complete: true,
      session_error: true,
      minion_comm: false,
      session_restart_error: true,
    })
  })

  it('getSettings() fills missing trayEvents keys from defaults when merging saved settings', async () => {
    localStorage.setItem('webui-notification-settings', JSON.stringify({
      trayEnabled: false,
      trayEvents: { minion_comm: true }
    }))

    const { getSettings } = await import('@/composables/useNotifications')
    const settings = getSettings()

    expect(settings.trayEnabled).toBe(false)
    expect(settings.trayEvents).toEqual({
      permission_prompt: true,
      task_complete: true,
      session_error: true,
      minion_comm: true,
      session_restart_error: true,
    })
  })

  it('updateSettings() partially merges trayEvents without clobbering other keys', async () => {
    const { updateSettings, getSettings } = await import('@/composables/useNotifications')

    updateSettings({ trayEvents: { session_error: false } })
    const settings = getSettings()

    expect(settings.trayEvents.session_error).toBe(false)
    expect(settings.trayEvents.task_complete).toBe(true)
  })

  it('getEventLabel() reuses the native title/body copy', async () => {
    const { getEventLabel } = await import('@/composables/useNotifications')

    const { title, body } = getEventLabel('task_complete', { sessionName: 'Alpha' })

    expect(title).toBe('Task Complete')
    expect(body).toBe('Alpha has completed processing')
  })

  it('notify() adds a tray entry when only the tray channel is active (sound/native off)', async () => {
    const { updateSettings, notify } = await import('@/composables/useNotifications')
    updateSettings({ trayEnabled: true, soundEnabled: false, ttsEnabled: false, nativeEnabled: false })

    notify('task_complete', { sessionName: 'Alpha' })
    await flushMicrotasks()

    expect(addEntryMock).toHaveBeenCalledWith('task_complete', { sessionName: 'Alpha' })
  })

  it('notify() does not add a tray entry when trayEnabled is false, even if sound is on', async () => {
    const { updateSettings, notify } = await import('@/composables/useNotifications')
    updateSettings({ trayEnabled: false, soundEnabled: true })

    notify('task_complete', { sessionName: 'Alpha' })
    await flushMicrotasks()

    expect(addEntryMock).not.toHaveBeenCalled()
  })

  it('notify() does not add a tray entry when the event type is excluded from trayEvents', async () => {
    const { updateSettings, notify } = await import('@/composables/useNotifications')
    updateSettings({ trayEnabled: true, trayEvents: { minion_comm: false } })

    notify('minion_comm', { fromMinion: 'Bob' })
    await flushMicrotasks()

    expect(addEntryMock).not.toHaveBeenCalled()
  })

  it('notify() fires sound/native channels independently of the tray channel', async () => {
    const { updateSettings, notify } = await import('@/composables/useNotifications')
    updateSettings({ trayEnabled: false, soundEnabled: true, ttsEnabled: false })

    // Should not throw even with tray disabled — sound channel handles its own path
    expect(() => notify('task_complete', { sessionName: 'Alpha' })).not.toThrow()
  })
})
