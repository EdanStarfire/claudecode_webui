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

describe('ProjectOverview - Stop All failure-path reconciliation (issue #1933)', () => {
  it('reconciles stopped sessions into stoppedSet when the halt-all request itself fails', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'ACTIVE' }))

    apiMock.post.mockRejectedValue(new Error('Stop All request timed out'))

    const { sessionStore } = await mountForResume(project, sessions, { stoppedIds: [] })

    vi.useFakeTimers()
    try {
      await fireEvent.click(screen.getByText('⏹ Stop All'))
      await fireEvent.click(screen.getByText('Confirm Stop All'))
      await vi.advanceTimersByTimeAsync(0)

      // The request itself already failed — the UI must unlock immediately rather
      // than staying spinner-locked for the whole (up to 130s) reconciliation window.
      expect(screen.getByText('⏹ Stop All')).toBeTruthy()
      expect(screen.queryByText('Stopping…')).toBeFalsy()

      // Backend keeps terminating in the background — s1 reaches TERMINATED during
      // the first reconciliation poll tick.
      sessionStore.sessions.set('s1', { ...sessionStore.getSession('s1'), state: 'TERMINATED' })

      await vi.advanceTimersByTimeAsync(2000)

      // Written progressively — visible immediately after the tick that detects it,
      // not only once the full bounded window elapses.
      expect(getStoppedSet('p1')).toEqual(['s1'])

      // s2 never terminates, so the poll loop keeps running until the bounded
      // window's deadline; only then does the reconciled toast get set.
      await vi.advanceTimersByTimeAsync(130000)

      expect(getStoppedSet('p1')).toEqual(['s1'])
      expect(screen.getByText(/1 of 2 sessions were confirmed stopped/)).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows a bare failure toast when the reconciliation window elapses with nothing confirmed stopped', async () => {
    const ids = ['s1']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'ACTIVE' }))

    apiMock.post.mockRejectedValue(new Error('network error'))

    await mountForResume(project, sessions, { stoppedIds: [] })

    vi.useFakeTimers()
    try {
      await fireEvent.click(screen.getByText('⏹ Stop All'))
      await fireEvent.click(screen.getByText('Confirm Stop All'))
      await vi.advanceTimersByTimeAsync(0)

      await vi.advanceTimersByTimeAsync(130000)

      expect(getStoppedSet('p1')).toEqual([])
      expect(screen.getByText(/✗ Stop All failed: network error/)).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('reconciliation window is at least as long as the relay layer\'s 120s halt-all timeout', async () => {
    // In the worst case the halt-all HTTP call doesn't fail client-side until it
    // hits src/routers/relay.py's 120s timeout override — the reconciliation
    // window has to outlast that, or it can never observe Backend actually
    // finish in exactly the scenario it exists for.
    const ids = ['s1']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'ACTIVE' }))

    apiMock.post.mockRejectedValue(new Error('network error'))

    await mountForResume(project, sessions, { stoppedIds: [] })

    vi.useFakeTimers()
    try {
      await fireEvent.click(screen.getByText('⏹ Stop All'))
      await fireEvent.click(screen.getByText('Confirm Stop All'))
      await vi.advanceTimersByTimeAsync(0)

      // Just under the relay's 120s ceiling — reconciliation must still be
      // in-flight, not have already given up and shown a final toast.
      await vi.advanceTimersByTimeAsync(118000)
      expect(screen.getByText(/checking which sessions actually stopped/)).toBeTruthy()

      // Past the full window — now it should conclude.
      await vi.advanceTimersByTimeAsync(15000)
      expect(screen.getByText(/✗ Stop All failed: network error/)).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('excludes already-TERMINATED sessions from the reconciliation target set', async () => {
    // s3 was stopped earlier for unrelated reasons — emergency_halt_all() on the
    // backend excludes pre-TERMINATED sessions from its own target set
    // (legion_coordinator.py), so the failure-path reconciliation must match.
    const project = makeProject({ project_id: 'p1', session_ids: ['s1', 's2', 's3'] })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's2', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's3', project_id: 'p1', state: 'TERMINATED' }),
    ]

    apiMock.post.mockRejectedValue(new Error('network error'))

    await mountForResume(project, sessions, { stoppedIds: [] })

    vi.useFakeTimers()
    try {
      await fireEvent.click(screen.getByText('⏹ Stop All'))
      await fireEvent.click(screen.getByText('Confirm Stop All'))
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(130000)

      // s3 must not be folded into stoppedSet just for already being terminated,
      // and the toast's "of M" denominator must reflect only the 2 real targets.
      expect(getStoppedSet('p1')).toEqual([])
      expect(screen.getByText(/✗ Stop All failed: network error/)).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('leaves the success-path stoppedSet/toast behavior unchanged when halt-all succeeds', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'ACTIVE' }))

    apiMock.post.mockResolvedValue({
      stopped_session_ids: ids,
      failed_sessions: [],
      total_sessions: 2,
    })

    await mountForResume(project, sessions, { stoppedIds: [] })

    await fireEvent.click(screen.getByText('⏹ Stop All'))
    await fireEvent.click(screen.getByText('Confirm Stop All'))
    await flush()

    expect(getStoppedSet('p1')).toEqual(ids)
    expect(screen.getByText(/✓ Stopped 2 sessions/)).toBeTruthy()
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

describe('ProjectOverview - Selection-based batch Stop/Start (issue #1934)', () => {
  it('Stop Selected terminates only the selected, non-terminated sessions and clears the selection', async () => {
    const ids = ['s1', 's2', 's3']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's2', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's3', project_id: 'p1', state: 'ACTIVE' }),
    ]
    apiMock.post.mockResolvedValue({})

    const { uiStore, sessionStore } = await mountForResume(project, sessions, { stoppedIds: [] })
    // Select s1 and s2 only — s3 stays untouched
    uiStore.toggleSessionSelection('s1')
    uiStore.toggleSessionSelection('s2')
    await flush()

    await fireEvent.click(screen.getByText('⏹ Stop Selected'))
    await fireEvent.click(screen.getByText('Confirm Stop Selected'))
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/s1/terminate')
    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/s2/terminate')
    expect(apiMock.post).not.toHaveBeenCalledWith('/api/sessions/s3/terminate')
    expect(getStoppedSet('p1').sort()).toEqual(['s1', 's2'])
    expect(uiStore.selectedSessionIds.size).toBe(0)
    expect(sessionStore.getSession('s3').state).toBe('ACTIVE')
    expect(screen.getByText(/✓ Stopped 2 of 2 selected/)).toBeTruthy()
  })

  it('Start Selected dispatches startSession for fresh stops and enqueues the resume message for mid-task stops', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', state: 'TERMINATED' }),
      makeSession({ session_id: 's2', project_id: 'p1', state: 'TERMINATED' }),
    ]
    apiMock.post.mockResolvedValue({})

    // s1 was mid-task when stopped (tracked in processingSet); s2 was a fresh stop
    const { uiStore } = await mountForResume(project, sessions, { stoppedIds: ids, processingIds: ['s1'] })
    uiStore.toggleSessionSelection('s1')
    uiStore.toggleSessionSelection('s2')
    await flush()

    await fireEvent.click(screen.getByText('↻ Start Selected'))
    await fireEvent.click(screen.getByText('Confirm Start Selected'))
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/s2/start')
    expect(apiMock.post).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/sessions\/s1\/(queue|messages)/),
      expect.anything()
    )
    expect(uiStore.selectedSessionIds.size).toBe(0)
  })

  it('excludes already-ACTIVE sessions from Stop Selected and surfaces the skipped count', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's2', project_id: 'p1', state: 'TERMINATED' }),
    ]
    apiMock.post.mockResolvedValue({})

    const { uiStore } = await mountForResume(project, sessions, { stoppedIds: [] })
    uiStore.toggleSessionSelection('s1')
    uiStore.toggleSessionSelection('s2')
    await flush()

    await fireEvent.click(screen.getByText('⏹ Stop Selected'))
    await fireEvent.click(screen.getByText('Confirm Stop Selected'))
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/s1/terminate')
    expect(apiMock.post).not.toHaveBeenCalledWith('/api/sessions/s2/terminate')
    expect(screen.getByText(/✓ Stopped 1 of 2 selected \(1 already stopped\)/)).toBeTruthy()
  })

  it('excludes already-ACTIVE sessions from Start Selected and surfaces the skipped count', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's2', project_id: 'p1', state: 'TERMINATED' }),
    ]
    apiMock.post.mockResolvedValue({})

    const { uiStore } = await mountForResume(project, sessions, { stoppedIds: ['s2'] })
    uiStore.toggleSessionSelection('s1')
    uiStore.toggleSessionSelection('s2')
    await flush()

    await fireEvent.click(screen.getByText('↻ Start Selected'))
    await fireEvent.click(screen.getByText('Confirm Start Selected'))
    await flush()

    expect(apiMock.post).toHaveBeenCalledWith('/api/sessions/s2/start')
    expect(apiMock.post).not.toHaveBeenCalledWith('/api/sessions/s1/start')
    expect(screen.getByText(/✓ Started 1 of 2 selected \(1 already active\)/)).toBeTruthy()
  })

  it('using Stop All does not mutate a pending selection, and vice versa', async () => {
    const ids = ['s1', 's2']
    const project = makeProject({ project_id: 'p1', session_ids: ids })
    const sessions = ids.map(id => makeSession({ session_id: id, project_id: 'p1', state: 'ACTIVE' }))
    apiMock.post.mockResolvedValue({ stopped_session_ids: ['s1', 's2'], failed_sessions: [], total_sessions: 2 })

    const { uiStore } = await mountForResume(project, sessions, { stoppedIds: [] })
    uiStore.toggleSessionSelection('s1')
    await flush()

    await fireEvent.click(screen.getByText('⏹ Stop All'))
    await fireEvent.click(screen.getByText('Confirm Stop All'))
    await flush()

    // Stop All's own success path doesn't touch selection state
    expect(uiStore.selectedSessionIds.has('s1')).toBe(true)
  })

  it('project-switch watcher clears the selection', async () => {
    const project1 = makeProject({ project_id: 'p1', session_ids: ['s1'] })
    const project2 = makeProject({ project_id: 'p2', session_ids: ['s2'] })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', state: 'ACTIVE' }),
      makeSession({ session_id: 's2', project_id: 'p2', state: 'ACTIVE' }),
    ]

    const { pinia, wrapper, uiStore } = await mountForResume(project1, sessions, { stoppedIds: [] })
    const { useProjectStore } = await import('@/stores/project')
    useProjectStore(pinia).projects.set('p2', project2)

    uiStore.toggleSessionSelection('s1')
    expect(uiStore.selectedSessionIds.size).toBe(1)

    await wrapper.setProps({ projectId: 'p2' })
    await flush()

    expect(uiStore.selectedSessionIds.size).toBe(0)
  })
})
