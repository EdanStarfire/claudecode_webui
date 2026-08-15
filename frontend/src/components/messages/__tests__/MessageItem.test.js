import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import { makeMessage } from '@/test-utils/factories'
import MessageItem from '@/components/messages/MessageItem.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
vi.mock('@/composables/useMarkdown', () => ({
  useMarkdownPlugins: () => [],
  useMarkdownComponents: () => ({}),
}))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('MessageItem', () => {
  it('renders user message content', async () => {
    renderWithStores(MessageItem, {
      props: {
        message: makeMessage({ type: 'user', content: 'Hello from user' }),
        attachedTools: []
      },
      stubs: {
        UserMessage: { template: '<div>{{ message.content }}</div>', props: ['message'] },
        AssistantMessage: true,
        SystemMessage: true,
        ActivityTimeline: true
      }
    })

    expect(screen.getByText('Hello from user')).toBeTruthy()
  })

  it('renders assistant message content', async () => {
    renderWithStores(MessageItem, {
      props: {
        message: makeMessage({ type: 'assistant', content: 'Hello from assistant' }),
        attachedTools: []
      },
      stubs: {
        UserMessage: true,
        AssistantMessage: { template: '<div>{{ message.content }}</div>', props: ['message', 'attachedTools'] },
        SystemMessage: true,
        ActivityTimeline: true
      }
    })

    expect(screen.getByText('Hello from assistant')).toBeTruthy()
  })

  // Issue #1746 (stage: layout)
  it('forwards mergedMessages through to AssistantMessage', async () => {
    const merged = [makeMessage({ type: 'assistant', content: 'Second turn' })]

    renderWithStores(MessageItem, {
      props: {
        message: makeMessage({ type: 'assistant', content: 'First turn' }),
        attachedTools: [],
        mergedMessages: merged
      },
      stubs: {
        UserMessage: true,
        AssistantMessage: {
          template: '<div data-testid="assistant-stub">{{ (mergedMessages || []).map(m => m.content).join(",") }}</div>',
          props: ['message', 'attachedTools', 'orphanedPermissionTools', 'mergedMessages']
        },
        SystemMessage: true,
        ActivityTimeline: true
      }
    })

    expect(screen.getByTestId('assistant-stub').textContent).toBe('Second turn')
  })

  it('defaults mergedMessages to an empty array when not provided', async () => {
    renderWithStores(MessageItem, {
      props: {
        message: makeMessage({ type: 'assistant', content: 'Solo turn' }),
        attachedTools: []
      },
      stubs: {
        UserMessage: true,
        AssistantMessage: {
          template: '<div data-testid="assistant-stub">{{ (mergedMessages || []).length }}</div>',
          props: ['message', 'attachedTools', 'orphanedPermissionTools', 'mergedMessages']
        },
        SystemMessage: true,
        ActivityTimeline: true
      }
    })

    expect(screen.getByTestId('assistant-stub').textContent).toBe('0')
  })
})
