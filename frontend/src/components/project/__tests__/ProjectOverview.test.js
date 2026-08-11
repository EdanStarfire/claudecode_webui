import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ProjectOverview from '@/components/project/ProjectOverview.vue'
import { makeProject, makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ id: 'root', name: 'User', children: [] })
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
