import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { render, screen } from '@testing-library/vue'
import { createPinia as _createPinia } from 'pinia'
import { ref } from 'vue'
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

const SESSION_ID = 'sess-1'
const viewSessionIdRef = ref(SESSION_ID)

beforeEach(() => {
  setActivePinia(createPinia())
  viewSessionIdRef.value = SESSION_ID
  apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })
})

describe('MessageList', () => {
  it('renders messages from the message store', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: { template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>', props: ['message', 'attachedTools'] },
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ content: 'First message' }),
      makeMessage({ content: 'Second message' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const items = screen.getAllByRole('article')
    expect(items.length).toBeGreaterThanOrEqual(2)
  })

  it('shows empty state when no session is selected', async () => {
    const nullSessionRef = ref(null)
    renderWithStores(MessageList, {
      provide: { viewSessionId: nullSessionRef },
      stubs: {
        MessageItem: true,
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    await new Promise(r => setTimeout(r, 10))

    // No article elements when no session
    expect(screen.queryAllByRole('article').length).toBe(0)
  })

  it('hides signature-only assistant messages from Auto mode', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: { template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>', props: ['message', 'attachedTools'] },
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_tool_uses: true,
          tool_uses: [{ tool_use_id: 'use-1', name: 'Bash', input: { command: 'ls' } }]
        }
      }),
      // Auto mode interstitial: empty thinking + signature blob, no tools
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_thinking: true,
          thinking_content: '',
          has_tool_uses: false,
          tool_uses: []
        }
      }),
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_tool_uses: true,
          tool_uses: [{ tool_use_id: 'use-2', name: 'Read', input: { file_path: '/tmp/x' } }]
        }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    // Signature message is filtered; both tool messages consolidate into one bubble
    expect(screen.getAllByRole('article').length).toBe(1)
  })

  it('preserves assistant messages with non-empty thinking content', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: { template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>', props: ['message', 'attachedTools'] },
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_thinking: true,
          thinking_content: 'Some thinking about the problem...',
          has_tool_uses: false,
          tool_uses: []
        }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    // Message with real thinking content must not be filtered out
    expect(screen.getAllByRole('article').length).toBe(1)
  })

  it('does not change grouping in non-Auto sequences', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: { template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>', props: ['message', 'attachedTools'] },
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_tool_uses: true,
          tool_uses: [{ tool_use_id: 'use-1', name: 'Bash', input: { command: 'ls' } }]
        }
      }),
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_tool_uses: true,
          tool_uses: [{ tool_use_id: 'use-2', name: 'Read', input: { file_path: '/tmp/a' } }]
        }
      }),
      makeMessage({
        type: 'assistant',
        content: 'Assistant response',
        metadata: {
          has_tool_uses: true,
          tool_uses: [{ tool_use_id: 'use-3', name: 'Write', input: { file_path: '/tmp/b', content: 'x' } }]
        }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    // Three consecutive tool-only messages consolidate into a single bubble (pre-fix behaviour preserved)
    expect(screen.getAllByRole('article').length).toBe(1)
  })
})
