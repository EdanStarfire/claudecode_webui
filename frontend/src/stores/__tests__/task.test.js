import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Issue #2030 (stage 1b): the Tasks panel is hydrated from the server snapshot
// (GET /api/sessions/{id}/tasks) and only LIVE tool_call records touch the task store.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-tasks'
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const CASES = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, '../../../../backend/tests/fixtures/task_reducer_cases.json'), 'utf-8'
)).cases

async function stores() {
  setActivePinia(createPinia())
  const { useMessageStore } = await import('@/stores/message')
  const { useTaskStore } = await import('@/stores/task')
  return { messageStore: useMessageStore(), taskStore: useTaskStore() }
}

function taskCall(id, name, input, result, status = 'completed') {
  return { type: 'tool_call', tool_use_id: id, name, input, status, result }
}

beforeEach(() => { apiMock.get.mockReset() })

describe('shared task reducer cases (parity with backend/tests/test_task_list.py)', () => {
  it.each(CASES.map(c => [c.name, c]))('%s', async (_name, testCase) => {
    const { messageStore, taskStore } = await stores()
    // Production delivers a tool's terminal record once; the server de-duplicates by id.
    const applied = new Set()
    for (const record of testCase.records) {
      const terminal = record.status === 'completed' || record.status === 'failed'
      if (terminal) {
        if (applied.has(record.tool_use_id)) continue
        applied.add(record.tool_use_id)
      }
      messageStore.applyRecord(SID, record, 'live')
    }
    expect(taskStore.tasksForSession(SID)).toEqual(testCase.expected)
  })
})

describe('history replay never touches the task store', () => {
  const create = taskCall('tc1', 'TaskCreate', { subject: 'A' }, 'Task #1 created')
  it.each(['load', 'archive'])('source=%s', async (source) => {
    const { messageStore, taskStore } = await stores()
    messageStore.applyRecord(SID, create, source)
    expect(taskStore.tasksForSession(SID)).toEqual([])
  })

  it('applyOlderPage never touches it either', async () => {
    const { messageStore, taskStore } = await stores()
    messageStore.applyOlderPage(SID, [create])
    expect(taskStore.tasksForSession(SID)).toEqual([])
  })
})

describe('taskStore.hydrate (server snapshot)', () => {
  const snapshotTask = (id, subject, status = 'pending') => ({
    id, subject, description: '', status, activeForm: null, owner: null, blocks: [], blockedBy: [], metadata: {},
  })

  it('replaces the session tasks with the snapshot (tasks created outside any window included)', async () => {
    const { taskStore } = await stores()
    apiMock.get.mockResolvedValue({ session_id: SID, tasks: [snapshotTask('1', 'Old create'), snapshotTask('2', 'Another', 'in_progress')] })
    await taskStore.hydrate(SID)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/sessions/${SID}/tasks`)
    expect(taskStore.tasksForSession(SID).map(t => [t.id, t.subject, t.status])).toEqual([['1', 'Old create', 'pending'], ['2', 'Another', 'in_progress']])
    expect(taskStore.hasTasks(SID)).toBe(true)
    expect(taskStore.isHydrating(SID)).toBe(false)
  })

  it('an empty snapshot clears stale tasks', async () => {
    const { messageStore, taskStore } = await stores()
    messageStore.applyRecord(SID, taskCall('x', 'TaskCreate', { subject: 'stale' }, 'Task #9 created'), 'live')
    apiMock.get.mockResolvedValue({ session_id: SID, tasks: [] })
    await taskStore.hydrate(SID)
    expect(taskStore.tasksForSession(SID)).toEqual([])
    expect(taskStore.hasTasks(SID)).toBe(false)
  })

  it('replays a live update that arrived while the snapshot was in flight (never reverted)', async () => {
    const { messageStore, taskStore } = await stores()
    let resolveSnapshot
    apiMock.get.mockReturnValue(new Promise(resolve => { resolveSnapshot = resolve }))
    const hydrating = taskStore.hydrate(SID)
    expect(taskStore.isHydrating(SID)).toBe(true)
    messageStore.applyRecord(SID, taskCall('u1', 'TaskUpdate', { taskId: '1', status: 'completed' }, 'Updated task #1'), 'live')
    // Buffered, not yet visible.
    expect(taskStore.tasksForSession(SID)).toEqual([])
    resolveSnapshot({ session_id: SID, tasks: [snapshotTask('1', 'Task one', 'in_progress')] })
    await hydrating
    expect(taskStore.tasksForSession(SID).map(t => [t.id, t.subject, t.status])).toEqual([['1', 'Task one', 'completed']])
  })

  it('buffers from beginHydration() (window applied) until the snapshot lands', async () => {
    const { messageStore, taskStore } = await stores()
    taskStore.beginHydration(SID)
    messageStore.applyRecord(SID, taskCall('c1', 'TaskCreate', { subject: 'live' }, 'Task #2 created'), 'live')
    apiMock.get.mockResolvedValue({ session_id: SID, tasks: [snapshotTask('1', 'from snapshot')] })
    await taskStore.hydrate(SID)
    expect(taskStore.tasksForSession(SID).map(t => t.subject)).toEqual(['from snapshot', 'live'])
  })

  it('surfaces a failed request as an explicit error state (no window-derived fallback)', async () => {
    const { taskStore } = await stores()
    apiMock.get.mockRejectedValue(new Error('boom'))
    await taskStore.hydrate(SID)
    expect(taskStore.hasHydrationError(SID)).toBe(true)
    expect(taskStore.tasksForSession(SID)).toEqual([])
    apiMock.get.mockResolvedValue({ session_id: SID, tasks: [snapshotTask('1', 'retry ok')] })
    await taskStore.hydrate(SID)
    expect(taskStore.hasHydrationError(SID)).toBe(false)
    expect(taskStore.tasksForSession(SID)).toHaveLength(1)
  })
})

describe('taskStore.hydrate and the right-panel auto-show', () => {
  it('a snapshot landing does not trip the auto-show suppress flag permanently, and sets it while applying', async () => {
    const { taskStore } = await stores()
    const { useUIStore } = await import('@/stores/ui')
    const uiStore = useUIStore()
    const seen = []
    const { watch } = await import('vue')
    // App.vue's watcher runs on the 0 -> N transition; it must see the suppress flag set.
    watch(() => taskStore.hasTasks(SID), (has, had) => { if (has && !had) seen.push(uiStore.suppressAutoShow) })
    apiMock.get.mockResolvedValue({ session_id: SID, tasks: [{ id: '1', subject: 'x', status: 'pending', blocks: [], blockedBy: [], metadata: {} }] })
    await taskStore.hydrate(SID)
    expect(seen).toEqual([true])
    expect(uiStore.suppressAutoShow).toBe(false)
  })
})

describe('taskStore.hydrate — overlapping and cleared requests', () => {
  const task = (id, subject, status = 'pending') => ({ id, subject, description: '', status, activeForm: null, owner: null, blocks: [], blockedBy: [], metadata: {} })

  it('only the newest overlapping request applies; a live update buffered during both is replayed on top of the newest', async () => {
    const { messageStore, taskStore } = await stores()
    const resolvers = []
    apiMock.get.mockImplementation(() => new Promise(resolve => resolvers.push(resolve)))
    const first = taskStore.hydrate(SID)
    const second = taskStore.hydrate(SID)
    messageStore.applyRecord(SID, taskCall('u1', 'TaskUpdate', { taskId: '1', status: 'completed' }, 'Updated task #1'), 'live')
    resolvers[0]({ session_id: SID, tasks: [task('1', 'stale snapshot')] }) // older request lands first: dropped
    await first
    expect(taskStore.tasksForSession(SID)).toEqual([])
    expect(taskStore.isHydrating(SID)).toBe(true)
    resolvers[1]({ session_id: SID, tasks: [task('1', 'fresh snapshot', 'in_progress')] })
    await second
    expect(taskStore.tasksForSession(SID).map(t => [t.subject, t.status])).toEqual([['fresh snapshot', 'completed']])
  })

  it('a snapshot in flight when the session is cleared does not resurrect tasks', async () => {
    const { taskStore } = await stores()
    let resolve
    apiMock.get.mockReturnValue(new Promise(r => { resolve = r }))
    const pending = taskStore.hydrate(SID)
    taskStore.clearTasks(SID)
    resolve({ session_id: SID, tasks: [task('1', 'pre-reset')] })
    await pending
    expect(taskStore.tasksForSession(SID)).toEqual([])
  })

  it('overlapping applications leave the auto-show suppress flag restored', async () => {
    const { taskStore } = await stores()
    const { useUIStore } = await import('@/stores/ui')
    apiMock.get.mockResolvedValue({ session_id: SID, tasks: [task('1', 'x')] })
    await Promise.all([taskStore.hydrate(SID), taskStore.hydrate('other-session')])
    expect(useUIStore().suppressAutoShow).toBe(false)
  })
})
