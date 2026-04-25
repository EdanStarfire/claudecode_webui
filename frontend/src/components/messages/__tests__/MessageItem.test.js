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
  useMarkdown: () => ({ renderMarkdown: (t) => t })
}))
vi.mock('@/composables/useMermaid', () => ({
  useMermaid: () => ({ renderMermaid: vi.fn() })
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
})
