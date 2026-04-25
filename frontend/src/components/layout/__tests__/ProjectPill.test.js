import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ProjectPill from '@/components/layout/ProjectPill.vue'
import { makeProject, makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('ProjectPill', () => {
  it('renders project name and session count', async () => {
    const project = makeProject({ project_id: 'p1', name: 'My Project', session_ids: ['sess-1'] })
    const session = makeSession({ session_id: 'sess-1', state: 'active' })

    const { pinia } = renderWithStores(ProjectPill, {
      props: { project, isActive: false, isBrowsing: false }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { useProjectStore } = await import('@/stores/project')
    const sessionStore = useSessionStore(pinia)
    const projectStore = useProjectStore(pinia)
    sessionStore.sessions.set('sess-1', session)
    projectStore.projects.set('p1', project)

    expect(screen.getByText('My Project')).toBeTruthy()
    // Session count displayed
    expect(screen.getByText('1')).toBeTruthy()
  })
})
