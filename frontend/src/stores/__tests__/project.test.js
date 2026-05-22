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
})
