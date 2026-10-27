import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ref } from 'vue'
import { renderWithStores } from '@/test-utils/render'
import { makeMessage } from '@/test-utils/factories'
import { stubResizeObserver } from '@/test-utils/mockResizeObserver'
import MessageList from '@/components/messages/MessageList.vue'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
const ttsMock = vi.hoisted(() => ({ queueNewMessage: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
vi.mock('@/composables/useMermaid', () => ({ useMermaid: () => ({ renderMermaid: vi.fn() }) }))
vi.mock('@/composables/useTTSReadAloud', () => ({
  useTTSReadAloud: () => ({ ...ttsMock, isEnabled: { value: true }, isPlaying: { value: false }, isPaused: { value: false }, currentMessageId: { value: null } }),
}))

const SESSION_ID = 'sess-tts'
let observer

beforeEach(() => {
  apiMock.get.mockResolvedValue({})
  ttsMock.queueNewMessage.mockReset()
  observer = stubResizeObserver()
})
afterEach(() => observer.restore())

const assistantMsg = (i) => makeMessage({
  id: `a${i}`, message_id: `a${i}`, type: 'assistant', content: `Reply ${i}`, timestamp: 1700000000 + i,
  metadata: { turn_id: `t${i}`, has_tool_uses: false, tool_uses: [] },
})

// Issue #2030: a backfilled older page grows the list at the head; read-aloud must only ever
// pick up genuinely NEW messages at the tail.
describe('read-aloud with prepended history (issue #2030)', () => {
  it('does not queue prepended (older) assistant messages, but still queues a newly appended one', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: ref(SESSION_ID) },
      stubs: { MessageItem: { template: '<div />', props: ['message', 'attachedTools'] }, TruncationBanner: true, SubagentTimeline: true },
    })
    const { useMessageStore } = await import('@/stores/message')
    const { useUIStore } = await import('@/stores/ui')
    const { useSessionStore } = await import('@/stores/session')
    const messageStore = useMessageStore(pinia)
    useUIStore(pinia).ttsReadAloudEnabled = true
    useSessionStore(pinia).currentSessionId = SESSION_ID

    const set = (messages) => { messageStore.messagesBySession.set(SESSION_ID, messages); messageStore.messagesBySession = new Map(messageStore.messagesBySession) }
    set([assistantMsg(5), assistantMsg(6)])
    await new Promise(r => setTimeout(r, 30))
    set([assistantMsg(5), assistantMsg(6), assistantMsg(7)]) // appended: first change after mount is treated as the initial load
    await new Promise(r => setTimeout(r, 30))
    ttsMock.queueNewMessage.mockReset()

    set([assistantMsg(3), assistantMsg(4), assistantMsg(5), assistantMsg(6), assistantMsg(7)]) // prepend
    await new Promise(r => setTimeout(r, 30))
    expect(ttsMock.queueNewMessage).not.toHaveBeenCalled()

    set([assistantMsg(3), assistantMsg(4), assistantMsg(5), assistantMsg(6), assistantMsg(7), assistantMsg(8)]) // append
    await new Promise(r => setTimeout(r, 30))
    expect(ttsMock.queueNewMessage).toHaveBeenCalledTimes(1)
    expect(ttsMock.queueNewMessage.mock.calls[0][0].message_id).toBe('a8')
  })
})
