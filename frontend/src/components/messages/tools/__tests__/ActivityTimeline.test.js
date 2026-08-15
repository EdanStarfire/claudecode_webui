import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import ActivityTimeline from '@/components/messages/tools/ActivityTimeline.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('ActivityTimeline', () => {
  it('renders one TimelineNode chip per tool call', async () => {
    const tools = [
      { id: 'use-1', name: 'Bash', input: { command: 'ls' }, status: 'completed', isExpanded: false },
      { id: 'use-2', name: 'Read', input: { file_path: '/tmp/f' }, status: 'completed', isExpanded: false }
    ]

    renderWithStores(ActivityTimeline, {
      props: { tools },
      stubs: {
        TimelineNode: { template: '<div data-testid="timeline-node" />', props: ['tool', 'isExpanded', 'compact'] },
        TimelineDetail: true,
        PermissionPrompt: true
      }
    })

    await new Promise(r => setTimeout(r, 0))

    const nodes = screen.getAllByTestId('timeline-node')
    expect(nodes.length).toBe(2)
  })

  // Issue #1746 (stage: layout) regression guard: TimelineNode's dot+label restyle into a
  // chip must not disturb ActivityTimeline's own click-to-expand / permission auto-expand
  // contract — that behavior belongs to stage 4 and must keep working unmodified here.
  it('click-to-expand still opens TimelineDetail for a completed tool', async () => {
    const user = userEvent.setup()
    const tools = [
      { id: 'use-1', name: 'Bash', input: { command: 'ls' }, status: 'completed', isExpanded: false }
    ]

    renderWithStores(ActivityTimeline, {
      props: { tools },
      stubs: {
        TimelineDetail: { template: '<div data-testid="timeline-detail" />', props: ['toolCall'] },
        PermissionPrompt: true
      }
    })

    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByTestId('timeline-detail')).toBeNull()

    await user.click(screen.getByTestId('timeline-node'))
    expect(screen.getByTestId('timeline-detail')).toBeTruthy()

    await user.click(screen.getByTestId('timeline-node'))
    expect(screen.queryByTestId('timeline-detail')).toBeNull()
  })

  it('auto-expands the permission prompt when a tool enters permission_required', async () => {
    const tools = [
      { id: 'use-1', name: 'Edit', input: { file_path: '/tmp/f' }, status: 'permission_required', backendStatus: 'awaiting_permission', isExpanded: false }
    ]

    renderWithStores(ActivityTimeline, {
      props: { tools },
      stubs: {
        TimelineDetail: true,
        PermissionPrompt: { template: '<div data-testid="permission-prompt-stub" />', props: ['toolCall'] }
      }
    })

    await new Promise(r => setTimeout(r, 0))

    expect(screen.getByTestId('permission-prompt-stub')).toBeTruthy()
  })
})
