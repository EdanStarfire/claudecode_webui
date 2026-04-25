import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { render, screen } from '@testing-library/vue'
import { createPinia as _createPinia } from 'pinia'
import { renderWithStores } from '@/test-utils/render'
import { makeMessage } from '@/test-utils/factories'
import MessageList from '@/components/messages/MessageList.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
vi.mock('@/composables/useTTSReadAloud', () => ({
  useTTSReadAloud: () => ({
    isEnabled: { value: false },
    isPlaying: { value: false },
    speak: vi.fn(),
    stop: vi.fn()
  })
}))
vi.mock('@/composables/useMermaid', () => ({
  useMermaid: () => ({ renderMermaid: vi.fn() })
}))

beforeEach(() => {
  setActivePinia(createPinia())
  apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })
})

describe('MessageList', () => {
  it('renders messages from the message store', async () => {
    const { pinia } = renderWithStores(MessageList, {
      stubs: {
        MessageItem: { template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>', props: ['message', 'attachedTools'] },
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const sessionStore = useSessionStore(pinia)
    const messageStore = useMessageStore(pinia)

    sessionStore.currentSessionId = 'sess-1'
    messageStore.messagesBySession.set('sess-1', [
      makeMessage({ content: 'First message' }),
      makeMessage({ content: 'Second message' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const items = screen.getAllByRole('article')
    expect(items.length).toBeGreaterThanOrEqual(2)
  })

  it('shows empty state when no session is selected', async () => {
    const { pinia } = renderWithStores(MessageList, {
      stubs: {
        MessageItem: true,
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.currentSessionId = null

    await new Promise(r => setTimeout(r, 10))

    // No article elements when no session
    expect(screen.queryAllByRole('article').length).toBe(0)
  })
})
