import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ProjectOverview from '@/components/project/ProjectOverview.vue'
import { makeProject, makeSession } from '@/test-utils/factories'
import { setStoppedSet, setProcessingSet, getStoppedSet, clearStoppedSet, clearProcessingSet } from '@/utils/stoppedSet'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ id: 'root', name: 'User', children: [] })
})

async function flush() {
  await new Promise(r => setTimeout(r, 0))
}

// Deferred-promise controller for api.post so tests can assert in-flight call counts
// between batches without relying on real timers.
function createDeferredPostMock() {
  const pending = [] // { url, resolve, reject }
  apiMock.post.mockImplementation((url) => {
    return new Promise((resolve, reject) => {
      pending.push({ url, resolve, reject })
    })
  })
  return {
    pending,
    resolveNext(n = pending.length, value = {}) {
      const toResolve = pending.splice(0, n)
      toResolve.forEach(p => p.resolve(value))
    },
    rejectMatching(predicate, error = new Error('failed')) {
      const idx = pending.findIndex(p => predicate(p.url))
      if (idx === -1) return
      const [p] = pending.splice(idx, 1)
      p.reject(error)
    }
  }
}

async function mountForResume(project, sessions, { stoppedIds, processingIds = [] } = {}) {
  const { useProjectStore } = await import('@/stores/project')
  const { useSessionStore } = await import('@/stores/session')
  const { useUIStore } = await import('@/stores/ui')

  clearStoppedSet(project.project_id)
  clearProcessingSet(project.project_id)
  setStoppedSet(project.project_id, stoppedIds)
  if (processingIds.length > 0) setProcessingSet(project.project_id, processingIds)

  const rendered = renderWithStores(ProjectOverview, {
    props: { projectId: project.project_id },
    routes: [{ path: '/', component: { template: '<div/>' } }]
  })

  const projectStore = useProjectStore(rendered.pinia)
  const sessionStore = useSessionStore(rendered.pinia)
  const uiStore = useUIStore(rendered.pinia)

  projectStore.projects.set(project.project_id, project)
  for (const s of sessions) sessionStore.sessions.set(s.session_id, s)
  await flush()

  return { ...rendered, projectStore, sessionStore, uiStore }
}

async function setBatchSizeAndConfirm(batchSize) {
  await fireEvent.click(screen.getByText(/Resume Sessions/))
  await flush()
  const input = screen.getByLabelText('Resume batch size')
  await fireEvent.update(input, String(batchSize))
  await fireEvent.click(screen.getByText('Confirm Resume'))
}

describe('ProjectOverview - Throttled Resume Sessions (issue #1733)', () => {
  it('resumes all sessions in a single batch when count is under the configured batch size', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['s1', 's2', 's3'] })
    const sessions = ['s1', 's2', 's3'].map(id => makeSession({ session_id: id, project_id: 'p1', state: 'TERMINATED' }))
    const deferred = createDeferredPostMock()

    await mountForResume(project, sessions, { stoppedIds: ['s1', 's2', 's3'] })
    await setBatchSizeAndConfirm(10)
    await flush()

    expect(deferred.pending.length).toBe(3)
  })

  it('chunks resume into sequential batches, with no batch exceeding the configured size', async () => {
    const ids = ['s1', 's2', 's3', 's4', 's5']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'TERMINATED' }))
    const deferred = createDeferredPostMock()

    await mountForResume(project, sessions, { stoppedIds: ids })
    await setBatchSizeAndConfirm(2)
    await flush()

    // First batch of 2 in flight; nothing more should have been dispatched yet
    expect(deferred.pending.length).toBe(2)

    deferred.resolveNext(2)
    await flush()
    expect(deferred.pending.length).toBe(2) // second batch of 2

    deferred.resolveNext(2)
    await flush()
    expect(deferred.pending.length).toBe(1) // final batch of 1

    deferred.resolveNext(1)
    await flush()
    expect(apiMock.post).toHaveBeenCalledTimes(5)
  })

  it('keeps a failed session in the stopped set for retry while pruning successes', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'TERMINATED' }))
    const deferred = createDeferredPostMock()

    await mountForResume(project, sessions, { stoppedIds: ids })
    await setBatchSizeAndConfirm(10)
    await flush()

    deferred.rejectMatching(url => url.includes('s1'))
    deferred.resolveNext()
    await flush()
    await flush()

    expect(getStoppedSet('p1')).toEqual(['s1'])
  })

  it('uses the per-operation override instead of the persistent default', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'TERMINATED' }))
    const deferred = createDeferredPostMock()

    const { uiStore } = await mountForResume(project, sessions, { stoppedIds: ids })
    uiStore.setResumeBatchSize(10) // persistent default stays high

    await setBatchSizeAndConfirm(1) // per-operation override
    await flush()

    // Only 1 in flight despite the persistent default of 10
    expect(deferred.pending.length).toBe(1)
  })

  it('counts queued (processing) and fresh sessions together against the same batch limit', async () => {
    const ids = ['s1', 's2', 's3', 's4']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'TERMINATED' }))
    const deferred = createDeferredPostMock()

    // s1, s2 were mid-processing when stopped (resume via queue-message); s3, s4 are fresh starts
    await mountForResume(project, sessions, { stoppedIds: ids, processingIds: ['s1', 's2'] })
    await setBatchSizeAndConfirm(2)
    await flush()

    expect(deferred.pending.length).toBe(2)
    deferred.resolveNext(2)
    await flush()
    expect(deferred.pending.length).toBe(2)
  })
})

async function mountInCustomFlatMode(project, sessions) {
  const { useProjectStore } = await import('@/stores/project')
  const { useSessionStore } = await import('@/stores/session')
  const { useUIStore } = await import('@/stores/ui')

  const rendered = renderWithStores(ProjectOverview, {
    props: { projectId: project.project_id },
    routes: [{ path: '/', component: { template: '<div/>' } }]
  })

  const projectStore = useProjectStore(rendered.pinia)
  const sessionStore = useSessionStore(rendered.pinia)
  const uiStore = useUIStore(rendered.pinia)

  projectStore.projects.set(project.project_id, project)
  for (const s of sessions) sessionStore.sessions.set(s.session_id, s)

  uiStore.setProjectViewMode('flat')
  uiStore.setFlatGroupMode('custom')
  await new Promise(r => setTimeout(r, 0))

  return { ...rendered, projectStore, sessionStore, uiStore }
}

describe('ProjectOverview - Custom kanban grouping (issue #1722)', () => {
  it('Unassigned bucket renders normally (not stuck in edit/delete state) with zero groups and zero assignments', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: ['s1'],
      kanban_groups: [],
      kanban_group_assignments: {}
    })
    const session = makeSession({ session_id: 's1', project_id: 'p1', name: 'Session One' })

    await mountInCustomFlatMode(project, [session])

    // Regression: editingGroupId/confirmingDeleteGroupId used to default to `null`,
    // which collided with Unassigned's groupId (also null), rendering the Unassigned
    // heading as an open rename input plus a stray delete-confirmation banner on
    // first load, with no click ever happening.
    expect(screen.getByText('Unassigned')).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeFalsy()
    expect(screen.queryByText(/Delete "Unassigned"/)).toBeFalsy()
  })

  it('empty custom groups still render with an empty-state hint (unlike status buckets)', async () => {
    // A non-empty Unassigned bucket keeps the empty-state hint text unique to "Urgent"
    // (Unassigned is also empty-rendered when it has zero members, which is correct —
    // just not what this assertion targets).
    const project = makeProject({
      project_id: 'p1',
      session_ids: ['s1'],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    })
    const session = makeSession({ session_id: 's1', project_id: 'p1', name: 'Session One' })

    await mountInCustomFlatMode(project, [session])

    expect(screen.getByText('Urgent')).toBeTruthy()
    expect(screen.getByText('No sessions in this group')).toBeTruthy()
  })

  it('switching away from and back to Custom preserves group state (groups live on the project object)', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: [],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    })

    const { uiStore } = await mountInCustomFlatMode(project, [])
    expect(screen.getByText('Urgent')).toBeTruthy()

    uiStore.setFlatGroupMode('status')
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByText('Urgent')).toBeFalsy()

    uiStore.setFlatGroupMode('custom')
    await new Promise(r => setTimeout(r, 0))
    expect(screen.getByText('Urgent')).toBeTruthy()
  })

  it('clicking a group name enters rename mode without disturbing Unassigned', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: [],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    })

    await mountInCustomFlatMode(project, [])

    await fireEvent.click(screen.getByText('Urgent'))

    const textbox = screen.getByRole('textbox')
    expect(textbox.value).toBe('Urgent')
    expect(screen.queryByText(/Delete "Unassigned"/)).toBeFalsy()
  })
})
