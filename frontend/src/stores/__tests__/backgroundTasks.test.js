import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeMessage } from '@/test-utils/factories'
import samples from '../../../../backend/tests/fixtures/background_tasks_samples.json'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const sample = (label, nth = 0) => samples.filter(s => s._sample.trim() === label)[nth]
const T1 = { task_id: 't1', task_type: 'local_bash', description: 'one' }
const T2 = { task_id: 't2', task_type: 'local_agent', description: 'two' }

function bgMessage(tasks) {
  return makeMessage({
    type: 'system',
    metadata: { subtype: 'background_tasks_changed', init_data: { tasks } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('backgroundTasks store (issue #2145)', () => {
  it('setTasks replaces; empty list removes the entry', async () => {
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const store = useBackgroundTasksStore()
    store.setTasks('s1', [T1, T2])
    expect(store.tasksForSession('s1')).toEqual([T1, T2])
    store.setTasks('s1', [T2])
    expect(store.tasksForSession('s1')).toEqual([T2])
    store.setTasks('s1', [])
    expect(store.tasksForSession('s1')).toEqual([])
  })

  it('tolerates unknown types, extra fields, malformed entries, non-list input', async () => {
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const store = useBackgroundTasksStore()
    store.setTasks('s1', [{ ...sample('S6').metadata.init_data.tasks[0] }, { description: 'no id' }, null])
    expect(store.tasksForSession('s1')).toHaveLength(1)
    expect(store.tasksForSession('s1')[0].task_type).toBe('local_workflow')
    store.setTasks('s1', 'garbage')
    expect(store.tasksForSession('s1')).toEqual([])
  })

  it('hydrate sets tasks from the endpoint; failure does not throw', async () => {
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const store = useBackgroundTasksStore()
    apiMock.get.mockResolvedValueOnce({ session_id: 's1', tasks: [T1] })
    await store.hydrate('s1')
    expect(apiMock.get).toHaveBeenCalledWith('/api/sessions/s1/background_tasks')
    expect(store.tasksForSession('s1')).toEqual([T1])
    apiMock.get.mockRejectedValueOnce(new Error('boom'))
    await expect(store.hydrate('s1')).resolves.toBeUndefined()
  })

  it('message.js applies live snapshots only (not load/archive)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const msgStore = useMessageStore()
    const store = useBackgroundTasksStore()
    msgStore.applyRecord('s1', bgMessage([T1]), 'load')
    msgStore.applyRecord('s1', bgMessage([T1]), 'archive')
    expect(store.tasksForSession('s1')).toEqual([])
    msgStore.applyRecord('s1', bgMessage([T1, T2]), 'live')
    expect(store.tasksForSession('s1')).toEqual([T1, T2])
    msgStore.applyRecord('s1', bgMessage([T2]), 'live')
    expect(store.tasksForSession('s1')).toEqual([T2])
  })

  it('production sample applies live without throwing (16 tasks, extra fields)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const msgStore = useMessageStore()
    const store = useBackgroundTasksStore()
    msgStore.applyRecord('s1', sample('S2'), 'live')
    expect(store.tasksForSession('s1')).toHaveLength(16)
  })

  it.each(['client_launched', 'session_failed'])('live %s clears', async (subtype) => {
    const { useMessageStore } = await import('@/stores/message')
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const msgStore = useMessageStore()
    const store = useBackgroundTasksStore()
    store.setTasks('s1', [T1])
    msgStore.applyRecord('s1', makeMessage({ type: 'system', metadata: { subtype } }), 'live')
    expect(store.tasksForSession('s1')).toEqual([])
  })

  it('other live system subtypes (e.g. status) do not clear', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { useBackgroundTasksStore } = await import('@/stores/backgroundTasks')
    const msgStore = useMessageStore()
    const store = useBackgroundTasksStore()
    store.setTasks('s1', [T1])
    msgStore.applyRecord('s1', makeMessage({ type: 'system', metadata: { subtype: 'status' } }), 'live')
    expect(store.tasksForSession('s1')).toEqual([T1])
  })
})
