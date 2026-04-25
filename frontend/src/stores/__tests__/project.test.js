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
})
