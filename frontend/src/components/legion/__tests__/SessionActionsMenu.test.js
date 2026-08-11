import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import SessionActionsMenu from '@/components/legion/SessionActionsMenu.vue'
import { makeSession, makeProject } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.post.mockResolvedValue({})
  apiMock.put.mockResolvedValue({ success: true })
})

async function openMenu() {
  const toggle = screen.getByRole('button', { name: /session actions/i })
  await fireEvent.click(toggle)
}

describe('SessionActionsMenu', () => {
  it('shows Mark Unread (not Mark Read) for a reviewed session with completed work', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: null }
    })
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({
      session_id: 'sess-1', last_completion_at: '2024-01-01T00:00:00Z'
    }))
    // Not unreviewed -> canMarkUnread true, canMarkRead false
    vi.spyOn(sessionStore, 'isUnreviewed').mockReturnValue(false)

    await openMenu()

    expect(screen.queryByRole('menuitem', { name: /mark unread/i })).toBeTruthy()
    expect(screen.queryByRole('menuitem', { name: /^mark read/i })).toBeFalsy()
  })

  it('shows Mark Read (not Mark Unread) for an unreviewed session with completed work', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: null }
    })
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({
      session_id: 'sess-1', last_completion_at: '2024-01-01T00:00:00Z'
    }))
    vi.spyOn(sessionStore, 'isUnreviewed').mockReturnValue(true)

    await openMenu()

    expect(screen.queryByRole('menuitem', { name: /^mark read/i })).toBeTruthy()
    expect(screen.queryByRole('menuitem', { name: /mark unread/i })).toBeFalsy()
  })

  it('hides both mark read/unread when the session has never completed work', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: null }
    })
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1', last_completion_at: null }))

    await openMenu()

    expect(screen.queryByRole('menuitem', { name: /^mark read/i })).toBeFalsy()
    expect(screen.queryByRole('menuitem', { name: /mark unread/i })).toBeFalsy()
  })

  it('clicking Mark Read calls sessionStore.markRead', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: null }
    })
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({
      session_id: 'sess-1', last_completion_at: '2024-01-01T00:00:00Z'
    }))
    vi.spyOn(sessionStore, 'isUnreviewed').mockReturnValue(true)
    const markReadSpy = vi.spyOn(sessionStore, 'markRead').mockResolvedValue()

    await openMenu()
    await fireEvent.click(screen.getByRole('menuitem', { name: /^mark read/i }))

    expect(markReadSpy).toHaveBeenCalledWith('sess-1')
  })

  it('Move to lists Unassigned plus other groups, excluding the session\'s current group', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: 'proj-1' }
    })
    const { useSessionStore } = await import('@/stores/session')
    const { useProjectStore } = await import('@/stores/project')
    const sessionStore = useSessionStore(pinia)
    const projectStore = useProjectStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    projectStore.projects.set('proj-1', makeProject({
      project_id: 'proj-1',
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }, { group_id: 'g2', name: 'Later' }],
      kanban_group_assignments: { 'sess-1': 'g1' }
    }))

    await openMenu()

    expect(screen.queryByRole('menuitem', { name: 'Unassigned' })).toBeTruthy()
    expect(screen.queryByRole('menuitem', { name: 'Later' })).toBeTruthy()
    expect(screen.queryByRole('menuitem', { name: 'Urgent' })).toBeFalsy()
  })

  it('Move to includes Unassigned-excluded when session already unassigned', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: 'proj-1' }
    })
    const { useSessionStore } = await import('@/stores/session')
    const { useProjectStore } = await import('@/stores/project')
    const sessionStore = useSessionStore(pinia)
    const projectStore = useProjectStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    projectStore.projects.set('proj-1', makeProject({
      project_id: 'proj-1',
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    }))

    await openMenu()

    expect(screen.queryByRole('menuitem', { name: 'Unassigned' })).toBeFalsy()
    expect(screen.queryByRole('menuitem', { name: 'Urgent' })).toBeTruthy()
  })

  it('clicking a Move to option calls projectStore.assignSessionKanbanGroup', async () => {
    const { pinia } = renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: 'proj-1' }
    })
    const { useSessionStore } = await import('@/stores/session')
    const { useProjectStore } = await import('@/stores/project')
    const sessionStore = useSessionStore(pinia)
    const projectStore = useProjectStore(pinia)
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))
    projectStore.projects.set('proj-1', makeProject({
      project_id: 'proj-1',
      kanban_groups: [{ group_id: 'g1', name: 'Urgent' }],
      kanban_group_assignments: {}
    }))
    const assignSpy = vi.spyOn(projectStore, 'assignSessionKanbanGroup').mockResolvedValue()

    await openMenu()
    await fireEvent.click(screen.getByRole('menuitem', { name: 'Urgent' }))

    expect(assignSpy).toHaveBeenCalledWith('proj-1', 'sess-1', 'g1')
  })

  it('hides the Move to section entirely when no projectId is provided', async () => {
    renderWithStores(SessionActionsMenu, {
      props: { sessionId: 'sess-1', projectId: null }
    })
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore()
    sessionStore.sessions.set('sess-1', makeSession({ session_id: 'sess-1' }))

    await openMenu()

    expect(screen.queryByText('Move to')).toBeFalsy()
  })
})
