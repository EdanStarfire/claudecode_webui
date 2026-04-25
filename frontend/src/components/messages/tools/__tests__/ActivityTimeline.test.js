import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
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
  it('renders one TimelineNode per tool call', async () => {
    const tools = [
      { id: 'use-1', name: 'Bash', input: { command: 'ls' }, status: 'completed', isExpanded: false },
      { id: 'use-2', name: 'Read', input: { file_path: '/tmp/f' }, status: 'completed', isExpanded: false }
    ]

    renderWithStores(ActivityTimeline, {
      props: { tools },
      stubs: {
        TimelineNode: { template: '<div data-testid="timeline-node" />', props: ['tool', 'isExpanded', 'compact'] },
        TimelineDetail: true,
        TimelineSegment: true,
        TimelineOverflow: true,
        PermissionPrompt: true
      }
    })

    await new Promise(r => setTimeout(r, 0))

    const nodes = screen.getAllByTestId('timeline-node')
    expect(nodes.length).toBe(2)
  })
})
