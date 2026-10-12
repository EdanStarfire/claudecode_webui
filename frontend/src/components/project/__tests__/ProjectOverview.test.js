import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
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

// Variant for tests under vi.useFakeTimers() — the shared flush() above relies
// on a real setTimeout, which never resolves once fake timers are active.
async function setBatchSizeAndConfirmFake(batchSize) {
  await fireEvent.click(screen.getByText(/Resume Sessions/))
  await vi.advanceTimersByTimeAsync(0)
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

    vi.useFakeTimers()
    try {
      await setBatchSizeAndConfirmFake(2)

      // First batch of 2 in flight; nothing more should have been dispatched yet
      expect(deferred.pending.length).toBe(2)

      deferred.resolveNext(2)
      await vi.advanceTimersByTimeAsync(5000) // default inter-batch delay
      expect(deferred.pending.length).toBe(2) // second batch of 2

      deferred.resolveNext(2)
      await vi.advanceTimersByTimeAsync(5000)
      expect(deferred.pending.length).toBe(1) // final batch of 1

      deferred.resolveNext(1)
      await vi.advanceTimersByTimeAsync(0)
      expect(apiMock.post).toHaveBeenCalledTimes(5)
    } finally {
      vi.useRealTimers()
    }
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

    vi.useFakeTimers()
    try {
      await setBatchSizeAndConfirmFake(2)

      expect(deferred.pending.length).toBe(2)
      deferred.resolveNext(2)
      await vi.advanceTimersByTimeAsync(5000)
      expect(deferred.pending.length).toBe(2)
    } finally {
      vi.useRealTimers()
    }
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

    const { container } = await mountInCustomFlatMode(project, [session])
    // Scoped to .flat-list: the issue #2050 filter bar's group <select> also renders
    // an "Unassigned" option, which would otherwise collide with the heading text.
    const flatList = within(container.querySelector('.flat-list'))

    // Regression: editingGroupId/confirmingDeleteGroupId used to default to `null`,
    // which collided with Unassigned's groupId (also null), rendering the Unassigned
    // heading as an open rename input plus a stray delete-confirmation banner on
    // first load, with no click ever happening.
    expect(flatList.getByText('Unassigned')).toBeTruthy()
    expect(flatList.queryByRole('textbox')).toBeFalsy()
    expect(flatList.queryByText(/Delete "Unassigned"/)).toBeFalsy()
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

    const { container } = await mountInCustomFlatMode(project, [session])
    const flatList = within(container.querySelector('.flat-list'))

    expect(flatList.getByText('Urgent')).toBeTruthy()
    expect(flatList.getByText('No sessions in this group')).toBeTruthy()
  })

  it('switching away from and back to Custom preserves group state (groups live on the project object)', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: [],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    })

    const { uiStore, container } = await mountInCustomFlatMode(project, [])
    // Scoped to .flat-list: the issue #2050 filter bar's group <select> renders an
    // "Urgent" option regardless of flatGroupMode, since the filter applies across
    // all grouping modes — only the heading inside .flat-list is mode-specific.
    const flatList = within(container.querySelector('.flat-list'))
    expect(flatList.getByText('Urgent')).toBeTruthy()

    uiStore.setFlatGroupMode('status')
    await new Promise(r => setTimeout(r, 0))
    expect(flatList.queryByText('Urgent')).toBeFalsy()

    uiStore.setFlatGroupMode('custom')
    await new Promise(r => setTimeout(r, 0))
    expect(flatList.getByText('Urgent')).toBeTruthy()
  })

  it('clicking a group name enters rename mode without disturbing Unassigned', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: [],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    })

    const { container } = await mountInCustomFlatMode(project, [])
    const flatList = within(container.querySelector('.flat-list'))

    await fireEvent.click(flatList.getByText('Urgent'))

    const textbox = flatList.getByRole('textbox')
    expect(textbox.value).toBe('Urgent')
    expect(flatList.queryByText(/Delete "Unassigned"/)).toBeFalsy()
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

describe('ProjectOverview - Hierarchy view User root row removal (issue #2060)', () => {
  it('renders top-level minions at indent 0 with no User root row', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['s1'] })
    const session = makeSession({ session_id: 's1', project_id: 'p1', name: 'Minion One' })

    apiMock.get.mockResolvedValue({
      id: 'root',
      name: 'User',
      children: [{ id: 's1', name: 'Minion One', type: 'minion', children: [] }],
    })

    const { container } = await mountForResume(project, [session], { stoppedIds: [] })

    expect(container.querySelector('.user-root-node')).toBeFalsy()

    const topLevelNode = container.querySelector('.minion-tree-node')
    expect(topLevelNode).toBeTruthy()
    expect(topLevelNode.style.marginLeft).toBe('0px')
  })
})

async function mountForFilter(project, sessions, { flatGroupMode = 'none' } = {}) {
  const rendered = await mountForResume(project, sessions, { stoppedIds: [] })
  rendered.uiStore.setProjectViewMode('flat')
  rendered.uiStore.setFlatGroupMode(flatGroupMode)
  await flush()
  return rendered
}

function filterBarOf(container) {
  return within(container.querySelector('.filter-bar'))
}

function flatListOf(container) {
  return within(container.querySelector('.flat-list'))
}

describe('ProjectOverview - Filter bar (issue #2050, Stage A)', () => {
  it('AND-combines search, status, and group filters', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: ['s1', 's2', 's3'],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: { s1: 'g1', s2: 'g1', s3: 'g1' },
    })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', role: 'Backend Dev', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Alice Jones', role: 'Backend Dev', state: 'terminated' }),
      makeSession({ session_id: 's3', project_id: 'p1', name: 'Bob Carter', role: 'Backend Dev', state: 'active' }),
    ]

    const { container } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await fireEvent.click(bar.getByText('Active'))
    await userEvent.selectOptions(bar.getByLabelText('Filter by custom group'), ['g1'])
    await flush()

    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.queryByText('Alice Jones')).toBeFalsy() // excluded by status
    expect(list.queryByText('Bob Carter')).toBeFalsy() // excluded by search
  })

  it('matches search case-insensitively against both name and role', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['s1', 's2'] })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', role: 'Backend Dev', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Bob Carter', role: 'Ops', state: 'active' }),
    ]

    const { container } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'ALICE')
    await flush()
    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.queryByText('Bob Carter')).toBeFalsy()

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'backend')
    await flush()
    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.queryByText('Bob Carter')).toBeFalsy()
  })

  it('filters to the implicit Unassigned bucket for sessions with no group assignment', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: ['s1', 's2'],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: { s2: 'g1' },
    })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Bob Carter', state: 'active' }),
    ]

    const { container } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await userEvent.selectOptions(bar.getByLabelText('Filter by custom group'), ['unassigned'])
    await flush()

    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.queryByText('Bob Carter')).toBeFalsy()
  })

  it('Clear filters resets all three dimensions and restores the unfiltered list', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['s1', 's2'] })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Bob Carter', state: 'terminated' }),
    ]

    const { container } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()
    expect(list.queryByText('Bob Carter')).toBeFalsy()
    expect(bar.getByText('Clear filters')).toBeTruthy()

    await fireEvent.click(bar.getByText('Clear filters'))
    await flush()

    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.getByText('Bob Carter')).toBeTruthy()
    expect(bar.queryByText('Clear filters')).toBeFalsy()
  })

  it('distinguishes the zero-sessions empty state from the zero-matches-filtered empty state', async () => {
    const emptyProject = makeProject({ project_id: 'p1', session_ids: [] })
    const { container: emptyContainer } = await mountForFilter(emptyProject, [])
    expect(flatListOf(emptyContainer).getByText('No sessions yet. Click "Add Session" to create one.')).toBeTruthy()

    const project = makeProject({ project_id: 'p2', session_ids: ['s1'] })
    const sessions = [makeSession({ session_id: 's1', project_id: 'p2', name: 'Alice Smith', state: 'active' })]
    const { container } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'nonexistent')
    await flush()

    expect(list.getByText('No sessions match the active filters.')).toBeTruthy()
    expect(list.queryByText('No sessions yet. Click "Add Session" to create one.')).toBeFalsy()
  })

  it('keeps a filtered-out session selected — selection always derives from unfiltered projectSessions', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['s1', 's2'] })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Bob Carter', state: 'active' }),
    ]

    const { container, uiStore } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)

    uiStore.toggleSessionSelection('s1')
    uiStore.toggleSessionSelection('s2')
    await flush()
    expect(screen.getByText('2 selected')).toBeTruthy()

    // Filter out s2 — selection state must be untouched by this
    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()

    expect(uiStore.selectedSessionIds.has('s2')).toBe(true)
    expect(screen.getByText('2 selected')).toBeTruthy()
  })

  it('keeps the filter bar visible in Hierarchy view (issue #2050, Stage B)', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['s1'] })
    const sessions = [makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', state: 'active' })]

    const { container, uiStore } = await mountForFilter(project, sessions)
    expect(container.querySelector('.filter-bar')).toBeTruthy()

    uiStore.setProjectViewMode('hierarchy')
    await flush()

    expect(container.querySelector('.filter-bar')).toBeTruthy()
  })

  it('distinguishes a custom group that is genuinely empty from one whose members are merely filtered out', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: ['s1', 's2'],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }, { group_id: 'g2', name: 'Empty Group' }],
      kanban_group_assignments: { s1: 'g1' },
    })
    // s2 stays unassigned and keeps matching the search, so the overall list is
    // never fully empty — this isolates the per-bucket (not the all-filtered-out) path.
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Bob Carter', state: 'active' }),
    ]

    const { container } = await mountForFilter(project, sessions, { flatGroupMode: 'custom' })
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'carter')
    await flush()

    // "Urgent" has a real member (Alice) hidden by the filter — distinct message from
    // "Empty Group", which has zero members regardless of filtering.
    expect(list.getByText('No sessions in this group match the active filters')).toBeTruthy()
    expect(list.getByText('No sessions in this group')).toBeTruthy()
    expect(list.getByText('Bob Carter')).toBeTruthy()
  })

  it('prunes a selected group filter when its group is deleted, restoring the full list', async () => {
    const project = makeProject({
      project_id: 'p1',
      session_ids: ['s1', 's2'],
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: { s1: 'g1' },
    })
    const sessions = [
      makeSession({ session_id: 's1', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 's2', project_id: 'p1', name: 'Bob Carter', state: 'active' }),
    ]

    const { container, projectStore } = await mountForFilter(project, sessions)
    const bar = filterBarOf(container)
    const list = flatListOf(container)

    await userEvent.selectOptions(bar.getByLabelText('Filter by custom group'), ['g1'])
    await flush()
    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.queryByText('Bob Carter')).toBeFalsy()

    // Group gets deleted out from under the active filter — simulate the store mutation
    // directly (same effect as projectStore.deleteKanbanGroup's success path).
    projectStore.projects.set('p1', { ...project, kanban_groups: [], kanban_group_assignments: {} })
    await flush()

    // The stale 'g1' selection must be pruned — otherwise it matches nothing and
    // silently hides every session with no visible cause.
    expect(list.getByText('Alice Smith')).toBeTruthy()
    expect(list.getByText('Bob Carter')).toBeTruthy()
  })
})

describe('ProjectOverview - Hierarchy view filtering (issue #2050, Stage B)', () => {
  async function mountHierarchy(project, sessions, hierarchyChildren) {
    apiMock.get.mockResolvedValue({ id: 'root', name: 'User', children: hierarchyChildren })
    const rendered = await mountForResume(project, sessions, { stoppedIds: [] })
    rendered.uiStore.setProjectViewMode('hierarchy')
    await flush()
    return rendered
  }

  function hierarchyTreeOf(container) {
    return within(container.querySelector('.hierarchy-tree'))
  }

  function cardFor(container, name) {
    return [...container.querySelectorAll('.minion-card')].find(c => c.textContent.includes(name))
  }

  it('dims the ancestor chain down to a matching deep descendant, prunes unrelated roots', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['parent', 'child', 'other'] })
    const sessions = [
      makeSession({ session_id: 'parent', project_id: 'p1', name: 'Parent One', state: 'active' }),
      makeSession({ session_id: 'child', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 'other', project_id: 'p1', name: 'Other Root', state: 'active' }),
    ]
    const hierarchy = [
      {
        id: 'parent', name: 'Parent One', type: 'minion',
        children: [{ id: 'child', name: 'Alice Smith', type: 'minion', children: [] }],
      },
      { id: 'other', name: 'Other Root', type: 'minion', children: [] },
    ]

    const { container } = await mountHierarchy(project, sessions, hierarchy)
    const bar = filterBarOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()

    const tree = hierarchyTreeOf(container)
    expect(tree.getByText('Parent One')).toBeTruthy()
    expect(tree.getByText('Alice Smith')).toBeTruthy()
    expect(tree.queryByText('Other Root')).toBeFalsy()

    expect(cardFor(container, 'Parent One').classList.contains('filter-dimmed')).toBe(true)
    expect(cardFor(container, 'Alice Smith').classList.contains('filter-dimmed')).toBe(false)
  })

  it('keeps a directly-matching root-level minion undimmed and prunes the unrelated root', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['r1', 'r2'] })
    const sessions = [
      makeSession({ session_id: 'r1', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
      makeSession({ session_id: 'r2', project_id: 'p1', name: 'Bob Carter', state: 'active' }),
    ]
    const hierarchy = [
      { id: 'r1', name: 'Alice Smith', type: 'minion', children: [] },
      { id: 'r2', name: 'Bob Carter', type: 'minion', children: [] },
    ]

    const { container } = await mountHierarchy(project, sessions, hierarchy)
    const bar = filterBarOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()

    const tree = hierarchyTreeOf(container)
    expect(tree.getByText('Alice Smith')).toBeTruthy()
    expect(tree.queryByText('Bob Carter')).toBeFalsy()
    expect(cardFor(container, 'Alice Smith').classList.contains('filter-dimmed')).toBe(false)
  })

  it('shows the Hierarchy empty state when the filter matches nothing', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['r1'] })
    const sessions = [makeSession({ session_id: 'r1', project_id: 'p1', name: 'Alice Smith', state: 'active' })]
    const hierarchy = [{ id: 'r1', name: 'Alice Smith', type: 'minion', children: [] }]

    const { container } = await mountHierarchy(project, sessions, hierarchy)
    const bar = filterBarOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'nonexistent')
    await flush()

    expect(screen.getByText('No sessions match the active filters.')).toBeTruthy()
    expect(container.querySelector('.hierarchy-tree')).toBeFalsy()
  })

  it('Clear filters restores the exact unfiltered tree, including dimmed ancestors', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['parent', 'child'] })
    const sessions = [
      makeSession({ session_id: 'parent', project_id: 'p1', name: 'Parent One', state: 'active' }),
      makeSession({ session_id: 'child', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
    ]
    const hierarchy = [
      {
        id: 'parent', name: 'Parent One', type: 'minion',
        children: [{ id: 'child', name: 'Alice Smith', type: 'minion', children: [] }],
      },
    ]

    const { container } = await mountHierarchy(project, sessions, hierarchy)
    const bar = filterBarOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()
    expect(cardFor(container, 'Parent One').classList.contains('filter-dimmed')).toBe(true)

    await fireEvent.click(bar.getByText('Clear filters'))
    await flush()

    expect(cardFor(container, 'Parent One').classList.contains('filter-dimmed')).toBe(false)
    expect(container.querySelectorAll('.minion-tree-node')).toHaveLength(2)
  })

  it('renders Hierarchy view unchanged when no filter is active — no node receives filter-dimmed', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['parent', 'child'] })
    const sessions = [
      makeSession({ session_id: 'parent', project_id: 'p1', name: 'Parent One', state: 'active' }),
      makeSession({ session_id: 'child', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
    ]
    const hierarchy = [
      {
        id: 'parent', name: 'Parent One', type: 'minion',
        children: [{ id: 'child', name: 'Alice Smith', type: 'minion', children: [] }],
      },
    ]

    const { container } = await mountHierarchy(project, sessions, hierarchy)

    expect(container.querySelectorAll('.filter-dimmed')).toHaveLength(0)
    expect(container.querySelectorAll('.minion-tree-node')).toHaveLength(2)
  })

  it('hides the Flat-specific "X of Y sessions shown" counter in Hierarchy view', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['parent', 'child'] })
    const sessions = [
      makeSession({ session_id: 'parent', project_id: 'p1', name: 'Parent One', state: 'active' }),
      makeSession({ session_id: 'child', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
    ]
    const hierarchy = [
      {
        id: 'parent', name: 'Parent One', type: 'minion',
        children: [{ id: 'child', name: 'Alice Smith', type: 'minion', children: [] }],
      },
    ]

    const { container } = await mountHierarchy(project, sessions, hierarchy)
    const bar = filterBarOf(container)

    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()

    // Hierarchy-view "shown" count isn't the same quantity as the flat match count
    // (dimmed ancestors are shown without being matches) — the counter stays Flat-only.
    expect(container.textContent).not.toMatch(/sessions shown/)
  })

  it('auto-expands a user-collapsed branch to reveal a filtered match inside it', async () => {
    const project = makeProject({ project_id: 'p1', session_ids: ['parent', 'child'] })
    const sessions = [
      makeSession({ session_id: 'parent', project_id: 'p1', name: 'Parent One', state: 'active' }),
      makeSession({ session_id: 'child', project_id: 'p1', name: 'Alice Smith', state: 'active' }),
    ]
    const hierarchy = [
      {
        id: 'parent', name: 'Parent One', type: 'minion',
        children: [{ id: 'child', name: 'Alice Smith', type: 'minion', children: [] }],
      },
    ]

    const { container, uiStore } = await mountHierarchy(project, sessions, hierarchy)
    uiStore.toggleMinionCollapse('parent')
    await flush()
    expect(hierarchyTreeOf(container).queryByText('Alice Smith')).toBeFalsy()

    const bar = filterBarOf(container)
    await fireEvent.update(bar.getByLabelText('Search sessions by name or role'), 'alice')
    await flush()

    expect(hierarchyTreeOf(container).getByText('Alice Smith')).toBeTruthy()
  })
})

describe('ProjectOverview - Row density toggle (issue #2061)', () => {
  it('toggle button switches uiStore.compactMinionTreeRows and updates active button state', async () => {
    const project = makeProject({ project_id: 'proj-1' })
    const sessions = [makeSession({ session_id: 'sess-1', project_id: 'proj-1', name: 'Minion One' })]
    const { container, uiStore } = await mountForResume(project, sessions, { stoppedIds: [] })

    uiStore.setCompactMinionTreeRows(true)
    await flush()

    const normalBtn = within(container).getByTitle('Normal row height')
    const compactBtn = within(container).getByTitle('Compact single-line rows')
    expect(compactBtn.className).toContain('btn-primary')
    expect(normalBtn.className).toContain('btn-outline-secondary')

    await fireEvent.click(normalBtn)
    expect(uiStore.compactMinionTreeRows).toBe(false)
    expect(normalBtn.className).toContain('btn-primary')
    expect(compactBtn.className).toContain('btn-outline-secondary')

    await fireEvent.click(compactBtn)
    expect(uiStore.compactMinionTreeRows).toBe(true)
  })

  it('is visible in both Hierarchy and Flat view modes', async () => {
    const project = makeProject({ project_id: 'proj-1' })
    const sessions = [makeSession({ session_id: 'sess-1', project_id: 'proj-1', name: 'Minion One' })]
    const { container, uiStore } = await mountForResume(project, sessions, { stoppedIds: [] })

    uiStore.setProjectViewMode('hierarchy')
    await flush()
    expect(within(container).getByTitle('Compact single-line rows')).toBeTruthy()

    uiStore.setProjectViewMode('flat')
    await flush()
    expect(within(container).getByTitle('Compact single-line rows')).toBeTruthy()
  })
})
