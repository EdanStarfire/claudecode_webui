import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
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

  // Issue #1767: TimelineDetail must render immediately beneath the pill that was
  // clicked, not after the whole list — regression guard for the fixed-position bug.
  it('renders the detail panel inline directly after the expanded tool\'s node', async () => {
    const user = userEvent.setup()
    const tools = [
      { id: 'use-a', name: 'Bash', input: { command: 'ls' }, status: 'completed', isExpanded: false },
      { id: 'use-b', name: 'Read', input: { file_path: '/tmp/b' }, status: 'completed', isExpanded: false },
      { id: 'use-c', name: 'Write', input: { file_path: '/tmp/c' }, status: 'completed', isExpanded: false }
    ]

    const { container } = renderWithStores(ActivityTimeline, {
      props: { tools },
      stubs: {
        TimelineNode: {
          template: '<div data-testid="timeline-node" :data-tool-id="tool.id" @click="$emit(\'click\')" />',
          props: ['tool', 'isExpanded', 'compact'],
          emits: ['click']
        },
        TimelineDetail: {
          template: '<div data-testid="timeline-detail" :data-tool-id="toolCall.id" />',
          props: ['toolCall']
        },
        PermissionPrompt: true
      }
    })

    await new Promise(r => setTimeout(r, 0))

    const rowChildren = () => Array.from(container.querySelector('.timeline-row').children)
      .map(el => ({ testId: el.dataset.testid, toolId: el.dataset.toolId }))

    const nodes = screen.getAllByTestId('timeline-node')
    await user.click(nodes[0])
    expect(rowChildren()).toEqual([
      { testId: 'timeline-node', toolId: 'use-a' },
      { testId: 'timeline-detail', toolId: 'use-a' },
      { testId: 'timeline-node', toolId: 'use-b' },
      { testId: 'timeline-node', toolId: 'use-c' }
    ])

    await user.click(screen.getAllByTestId('timeline-node')[1])
    expect(rowChildren()).toEqual([
      { testId: 'timeline-node', toolId: 'use-a' },
      { testId: 'timeline-node', toolId: 'use-b' },
      { testId: 'timeline-detail', toolId: 'use-b' },
      { testId: 'timeline-node', toolId: 'use-c' }
    ])
  })

  // Issue #1748 (stage: windowing) review fix regression guard: expandedNodeId survives an
  // unmount/remount (store-backed), but the auto-collapse-on-resolve bookkeeping
  // (expandedForPermission) must ALSO survive it, or a permission resolved off-screen while a
  // row is unmounted (e.g. via PermissionQueue's always-mounted floating panel) leaves a stale
  // expanded panel stuck open forever once the row remounts.
  it('auto-collapses a permission-triggered expand after an unmount/remount once the permission resolves off-screen', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)

    const permTool = { id: 'use-1', name: 'Edit', input: { file_path: '/tmp/f' }, status: 'permission_required', backendStatus: 'awaiting_permission' }
    const stubs = { TimelineDetail: true, PermissionPrompt: true }

    const first = mount(ActivityTimeline, {
      global: { plugins: [pinia], stubs },
      props: { tools: [permTool], messageId: 'msg-1' }
    })
    await new Promise(r => setTimeout(r, 0))
    // Auto-expanded because it needs permission.
    expect(first.find('[data-testid="timeline-node"]').classes()).toContain('row-expanded')

    // Row genuinely unmounts (scrolled out of the virtualizer's overscan window).
    first.unmount()

    // Permission resolves off-screen (e.g. via PermissionQueue, which stays mounted regardless
    // of virtualization) — the store now reflects a completed tool, matching what the row's own
    // ActivityTimeline will see once it remounts.
    const resolvedTool = { ...permTool, status: 'completed', backendStatus: 'completed' }

    // Row remounts — a FRESH component instance, same Pinia store.
    const second = mount(ActivityTimeline, {
      global: { plugins: [pinia], stubs },
      props: { tools: [resolvedTool], messageId: 'msg-1' }
    })
    await new Promise(r => setTimeout(r, 0))

    expect(second.find('[data-testid="timeline-node"]').classes()).not.toContain('row-expanded')
  })
})
