import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeProject, makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('project store', () => {
  it('fetchProjects populates map and orderedProjects sorts by order', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const store = useProjectStore()

    const p1 = makeProject({ project_id: 'p1', order: 1 })
    const p2 = makeProject({ project_id: 'p2', order: 0 })
    apiMock.get.mockResolvedValue({ projects: [p1, p2] })

    await store.fetchProjects()

    expect(store.projects.size).toBe(2)
    expect(store.orderedProjects[0].project_id).toBe('p2')
    expect(store.orderedProjects[1].project_id).toBe('p1')
  })

  it('createProject adds new project to map', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const store = useProjectStore()

    const project = makeProject({ project_id: 'p-new' })
    apiMock.post.mockResolvedValue({ project })

    const result = await store.createProject('My Project', '/tmp')

    expect(apiMock.post).toHaveBeenCalledWith('/api/projects', expect.objectContaining({ name: 'My Project' }))
    expect(store.projects.has('p-new')).toBe(true)
    expect(result.project_id).toBe('p-new')
  })

  it('reorderProjects updates order fields', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const store = useProjectStore()

    store.projects.set('p1', makeProject({ project_id: 'p1', order: 0 }))
    store.projects.set('p2', makeProject({ project_id: 'p2', order: 1 }))
    apiMock.put.mockResolvedValue({})

    await store.reorderProjects(['p2', 'p1'])

    expect(store.projects.get('p2').order).toBe(0)
    expect(store.projects.get('p1').order).toBe(1)
  })

  it('getStatusBarSegments returns segment per session', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const { useSessionStore } = await import('@/stores/session')
    const store = useProjectStore()
    const sessionStore = useSessionStore()

    const session = makeSession({ session_id: 'sess-1', state: 'active', is_processing: false })
    sessionStore.sessions.set('sess-1', session)

    const project = makeProject({ project_id: 'p1', session_ids: ['sess-1'] })
    store.projects.set('p1', project)

    const segments = store.getStatusBarSegments('p1', sessionStore)

    expect(segments.length).toBe(1)
    expect(segments[0].status).toBe('idle')
  })

  it('getStatusBarSegments alpha sort orders top-level segments alphabetically', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const { useSessionStore } = await import('@/stores/session')
    const store = useProjectStore()
    const sessionStore = useSessionStore()

    const charlie = makeSession({ session_id: 's-charlie', name: 'Charlie', order: 0 })
    const alice = makeSession({ session_id: 's-alice', name: 'Alice', order: 1 })
    const bob = makeSession({ session_id: 's-bob', name: 'Bob', order: 2 })
    sessionStore.sessions.set('s-charlie', charlie)
    sessionStore.sessions.set('s-alice', alice)
    sessionStore.sessions.set('s-bob', bob)

    const project = makeProject({ project_id: 'p1', session_ids: ['s-charlie', 's-alice', 's-bob'] })
    store.projects.set('p1', project)

    const alphaSegs = store.getStatusBarSegments('p1', sessionStore, 'alpha')
    expect(alphaSegs.map(s => s.name)).toEqual(['Alice', 'Bob', 'Charlie'])

    const creationSegs = store.getStatusBarSegments('p1', sessionStore, 'creation')
    expect(creationSegs.map(s => s.name)).toEqual(['Charlie', 'Alice', 'Bob'])
  })

  it('getStatusBarSegments sorts children by the same mode', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const { useSessionStore } = await import('@/stores/session')
    const store = useProjectStore()
    const sessionStore = useSessionStore()

    const parent = makeSession({ session_id: 's-parent', name: 'Parent', order: 0, child_minion_ids: ['s-c', 's-a', 's-b'] })
    const childC = makeSession({ session_id: 's-c', name: 'Charlie', order: 0 })
    const childA = makeSession({ session_id: 's-a', name: 'Alice', order: 1 })
    const childB = makeSession({ session_id: 's-b', name: 'Bob', order: 2 })
    sessionStore.sessions.set('s-parent', parent)
    sessionStore.sessions.set('s-c', childC)
    sessionStore.sessions.set('s-a', childA)
    sessionStore.sessions.set('s-b', childB)

    const project = makeProject({ project_id: 'p1', session_ids: ['s-parent', 's-c', 's-a', 's-b'] })
    store.projects.set('p1', project)

    const alphaSegs = store.getStatusBarSegments('p1', sessionStore, 'alpha')
    expect(alphaSegs.map(s => s.name)).toEqual(['Parent', 'Alice', 'Bob', 'Charlie'])

    const creationSegs = store.getStatusBarSegments('p1', sessionStore, 'creation')
    expect(creationSegs.map(s => s.name)).toEqual(['Parent', 'Charlie', 'Alice', 'Bob'])
  })

  it('getStatusBarSegments uses session_id as stable tie-breaker for equal order', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const { useSessionStore } = await import('@/stores/session')
    const store = useProjectStore()
    const sessionStore = useSessionStore()

    const sA = makeSession({ session_id: 'aaa', name: 'X', order: 5 })
    const sB = makeSession({ session_id: 'bbb', name: 'X', order: 5 })
    sessionStore.sessions.set('aaa', sA)
    sessionStore.sessions.set('bbb', sB)

    const project = makeProject({ project_id: 'p1', session_ids: ['bbb', 'aaa'] })
    store.projects.set('p1', project)

    const seg1 = store.getStatusBarSegments('p1', sessionStore, 'creation')
    const seg2 = store.getStatusBarSegments('p1', sessionStore, 'creation')
    expect(seg1.map(s => s.name)).toEqual(seg2.map(s => s.name))
    // aaa sorts before bbb lexicographically
    expect(seg1[0].status).toBeDefined()
    expect(seg1.length).toBe(2)
  })

  it('getStatusBarSegments matches compareAgents ordering for both modes', async () => {
    const { useProjectStore } = await import('@/stores/project')
    const { useSessionStore } = await import('@/stores/session')
    const { compareAgents } = await import('@/utils/agentSort')
    const store = useProjectStore()
    const sessionStore = useSessionStore()

    const sessions = [
      makeSession({ session_id: 's1', name: 'Zebra', order: 0 }),
      makeSession({ session_id: 's2', name: 'Apple', order: 1 }),
      makeSession({ session_id: 's3', name: 'Mango', order: 2 }),
    ]
    for (const s of sessions) sessionStore.sessions.set(s.session_id, s)

    const project = makeProject({ project_id: 'p1', session_ids: sessions.map(s => s.session_id) })
    store.projects.set('p1', project)

    const accessors = { nameOf: s => s.name, orderOf: s => s.order, idOf: s => s.session_id }

    for (const mode of ['alpha', 'creation']) {
      const barOrder = store.getStatusBarSegments('p1', sessionStore, mode).map(s => s.name)
      const stripOrder = [...sessions]
        .sort((a, b) => compareAgents(mode, a, b, accessors))
        .map(s => s.name)
      expect(barOrder).toEqual(stripOrder)
    }
  })

  describe('kanban groups (issue #1722)', () => {
    it('createKanbanGroup patches local project from the response (server-generated group_id)', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const store = useProjectStore()

      const project = makeProject({ project_id: 'p1', kanban_groups: [] })
      store.projects.set('p1', project)

      const updatedProject = makeProject({
        project_id: 'p1',
        kanban_groups: [{ group_id: 'g1', name: 'Urgent' }]
      })
      apiMock.post.mockResolvedValue({ success: true, project: updatedProject })

      const result = await store.createKanbanGroup('p1', 'Urgent')

      expect(apiMock.post).toHaveBeenCalledWith('/api/projects/p1/kanban-groups', { name: 'Urgent' })
      expect(store.projects.get('p1').kanban_groups).toEqual([{ group_id: 'g1', name: 'Urgent' }])
      expect(result.kanban_groups).toEqual([{ group_id: 'g1', name: 'Urgent' }])
    })

    it('renameKanbanGroup patches only the matching group name locally', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const store = useProjectStore()

      const project = makeProject({
        project_id: 'p1',
        kanban_groups: [{ group_id: 'g1', name: 'Urgent' }, { group_id: 'g2', name: 'Later' }]
      })
      store.projects.set('p1', project)
      apiMock.put.mockResolvedValue({ success: true })

      await store.renameKanbanGroup('p1', 'g1', 'Renamed')

      expect(apiMock.put).toHaveBeenCalledWith('/api/projects/p1/kanban-groups/g1', { name: 'Renamed' })
      expect(store.projects.get('p1').kanban_groups).toEqual([
        { group_id: 'g1', name: 'Renamed' },
        { group_id: 'g2', name: 'Later' }
      ])
    })

    it('deleteKanbanGroup removes the group and strips matching assignments locally', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const store = useProjectStore()

      const project = makeProject({
        project_id: 'p1',
        kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
        kanban_group_assignments: { 'sess-1': 'g1', 'sess-2': 'g1' }
      })
      store.projects.set('p1', project)
      apiMock.delete.mockResolvedValue({ success: true })

      await store.deleteKanbanGroup('p1', 'g1')

      expect(apiMock.delete).toHaveBeenCalledWith('/api/projects/p1/kanban-groups/g1')
      const updated = store.projects.get('p1')
      expect(updated.kanban_groups).toEqual([])
      expect(updated.kanban_group_assignments).toEqual({})
    })

    it('reorderKanbanGroups reorders local groups by the given id sequence', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const store = useProjectStore()

      const project = makeProject({
        project_id: 'p1',
        kanban_groups: [{ group_id: 'g1', name: 'First' }, { group_id: 'g2', name: 'Second' }]
      })
      store.projects.set('p1', project)
      apiMock.put.mockResolvedValue({ success: true })

      await store.reorderKanbanGroups('p1', ['g2', 'g1'])

      expect(apiMock.put).toHaveBeenCalledWith('/api/projects/p1/kanban-groups/reorder', { group_ids: ['g2', 'g1'] })
      expect(store.projects.get('p1').kanban_groups.map(g => g.group_id)).toEqual(['g2', 'g1'])
    })

    it('assignSessionKanbanGroup writes and clears assignment map entries locally', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const store = useProjectStore()

      const project = makeProject({ project_id: 'p1', kanban_group_assignments: {} })
      store.projects.set('p1', project)
      apiMock.put.mockResolvedValue({ success: true })

      await store.assignSessionKanbanGroup('p1', 'sess-1', 'g1')
      expect(apiMock.put).toHaveBeenCalledWith(
        '/api/projects/p1/sessions/sess-1/kanban-group', { group_id: 'g1' }
      )
      expect(store.projects.get('p1').kanban_group_assignments).toEqual({ 'sess-1': 'g1' })

      await store.assignSessionKanbanGroup('p1', 'sess-1', null)
      expect(store.projects.get('p1').kanban_group_assignments).toEqual({})
    })
  })

  describe('getAttentionSummary (issue #1828)', () => {
    function unreadSession(overrides = {}) {
      return makeSession({
        last_completion_at: '2026-01-01T00:00:00.000Z',
        last_viewed_at: null,
        ...overrides
      })
    }

    it('returns [] when no non-browsing project has a qualifying session (T2)', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      const session = makeSession({ session_id: 'sess-1', state: 'active' })
      sessionStore.sessions.set('sess-1', session)
      store.projects.set('p1', makeProject({ project_id: 'p1', session_ids: ['sess-1'] }))

      expect(store.getAttentionSummary(sessionStore, 'creation', null)).toEqual([])
    })

    it('returns one entry with reasons: [waiting] for a single background project with a paused session (T1)', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      const session = makeSession({ session_id: 'sess-1', state: 'paused' })
      sessionStore.sessions.set('sess-1', session)
      store.projects.set('p1', makeProject({ project_id: 'p1', name: 'Data Pipeline', session_ids: ['sess-1'] }))

      const summary = store.getAttentionSummary(sessionStore, 'creation', 'other-project')
      expect(summary).toEqual([{ projectId: 'p1', name: 'Data Pipeline', reasons: ['waiting'] }])
    })

    it('a project with two sessions in different qualifying states returns one entry with both reasons', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      const paused = makeSession({ session_id: 'sess-1', state: 'paused' })
      const unread = unreadSession({ session_id: 'sess-2', state: 'active' })
      sessionStore.sessions.set('sess-1', paused)
      sessionStore.sessions.set('sess-2', unread)
      store.projects.set('p1', makeProject({ project_id: 'p1', name: 'Data Pipeline', session_ids: ['sess-1', 'sess-2'] }))

      const summary = store.getAttentionSummary(sessionStore, 'creation', null)
      expect(summary).toEqual([{ projectId: 'p1', name: 'Data Pipeline', reasons: ['waiting', 'unread'] }])
    })

    it('excludes the project matching excludeProjectId even if it has qualifying sessions', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      const session = makeSession({ session_id: 'sess-1', state: 'paused' })
      sessionStore.sessions.set('sess-1', session)
      store.projects.set('p1', makeProject({ project_id: 'p1', session_ids: ['sess-1'] }))

      expect(store.getAttentionSummary(sessionStore, 'creation', 'p1')).toEqual([])
    })

    it('returns multiple entries, one per affected project, when several background projects qualify (T4)', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1', state: 'paused' }))
      sessionStore.sessions.set('sess-2', unreadSession({ session_id: 'sess-2', state: 'active' }))
      sessionStore.sessions.set('sess-3', makeSession({ session_id: 'sess-3', state: 'error' }))
      store.projects.set('p1', makeProject({ project_id: 'p1', name: 'Data Pipeline', session_ids: ['sess-1'] }))
      store.projects.set('p2', makeProject({ project_id: 'p2', name: 'Mobile App', session_ids: ['sess-2'] }))
      store.projects.set('p3', makeProject({ project_id: 'p3', name: 'Docs Site', session_ids: ['sess-3'] }))

      const summary = store.getAttentionSummary(sessionStore, 'creation', null)
      expect(summary).toEqual([
        { projectId: 'p1', name: 'Data Pipeline', reasons: ['waiting'] },
        { projectId: 'p2', name: 'Mobile App', reasons: ['unread'] },
        { projectId: 'p3', name: 'Docs Site', reasons: ['error'] },
      ])
    })

    it('includes error state as a qualifying reason', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1', state: 'error' }))
      store.projects.set('p1', makeProject({ project_id: 'p1', name: 'Docs Site', session_ids: ['sess-1'] }))

      const summary = store.getAttentionSummary(sessionStore, 'creation', null)
      expect(summary).toEqual([{ projectId: 'p1', name: 'Docs Site', reasons: ['error'] }])
    })

    it('entry disappears once the qualifying session state clears (mirrors T3 live-clear)', async () => {
      const { useProjectStore } = await import('@/stores/project')
      const { useSessionStore } = await import('@/stores/session')
      const store = useProjectStore()
      const sessionStore = useSessionStore()

      const session = makeSession({ session_id: 'sess-1', state: 'paused' })
      sessionStore.sessions.set('sess-1', session)
      store.projects.set('p1', makeProject({ project_id: 'p1', name: 'Data Pipeline', session_ids: ['sess-1'] }))

      expect(store.getAttentionSummary(sessionStore, 'creation', null)).toEqual([
        { projectId: 'p1', name: 'Data Pipeline', reasons: ['waiting'] }
      ])

      sessionStore.sessions.get('sess-1').state = 'active'

      expect(store.getAttentionSummary(sessionStore, 'creation', null)).toEqual([])
    })
  })
})
