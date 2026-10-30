import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { render, screen, fireEvent } from '@testing-library/vue'
import { createPinia as _createPinia } from 'pinia'
import { ref } from 'vue'
import { renderWithStores } from '@/test-utils/render'
import { makeMessage } from '@/test-utils/factories'
import { stubResizeObserver } from '@/test-utils/mockResizeObserver'
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
    isPaused: { value: false },
    currentMessageId: { value: null },
    playMessage: vi.fn(),
    queueNewMessage: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    speak: vi.fn(),
    stop: vi.fn()
  })
}))
vi.mock('@/composables/useMermaid', () => ({
  useMermaid: () => ({ renderMermaid: vi.fn() })
}))

const SESSION_ID = 'sess-1'
const viewSessionIdRef = ref(SESSION_ID)

let resizeObserverStub

beforeEach(() => {
  setActivePinia(createPinia())
  viewSessionIdRef.value = SESSION_ID
  apiMock.get.mockResolvedValue({ messages: [], total_count: 0, has_more: false })
  // Issue #1748 (stage: offset-model): MessageList is now virtualizer-driven — see
  // mockResizeObserver.js for why jsdom needs this to render any rows at all.
  resizeObserverStub = stubResizeObserver()
})

afterEach(() => {
  resizeObserverStub.restore()
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

    // Issue #1746 (stage: layout): second message is 'user' rather than the default
    // 'assistant' so mergeConsecutiveAssistantTurns() doesn't fold them into one item —
    // this test is about basic store rendering, not merge behavior (see the dedicated
    // 'mergeConsecutiveAssistantTurns' describe block below for that).
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ content: 'First message' }),
      makeMessage({ type: 'user', content: 'Second message' })
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

  it('hides forwarded subagent assistant messages (parent_tool_use_id) from the main timeline', async () => {
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
        content: 'Top-level assistant reply',
        metadata: { has_tool_uses: false, tool_uses: [] }
      }),
      // Forwarded subagent text (forward_subagent_text, issue #1671/#1900) — must not leak as a top-level bubble
      makeMessage({
        type: 'assistant',
        content: 'Subagent thinking out loud',
        metadata: { parent_tool_use_id: 'toolu_task1', has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const items = screen.getAllByRole('article')
    expect(items.length).toBe(1)
    expect(items[0].textContent).toBe('Top-level assistant reply')
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

  it('hides skill re-invocation notice from the timeline (#1724)', async () => {
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
        type: 'user',
        content: '(Re-invocation of /my-skill — the skill instructions were previously loaded; the arguments or dynamic output below are new.)\nsome new args'
      }),
      makeMessage({ type: 'assistant', content: 'Real assistant reply' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const items = screen.getAllByRole('article')
    expect(items.length).toBe(1)
    expect(items[0].textContent).toBe('Real assistant reply')
  })

  it('hides background_tasks_changed bubbles but still shows other system messages (#2145)', async () => {
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
      makeMessage({ type: 'system', content: 'Claude Code Launched', metadata: { subtype: 'client_launched' } }),
      makeMessage({
        type: 'system', content: 'BG bubble',
        metadata: { subtype: 'background_tasks_changed', init_data: { tasks: [{ task_id: 't1', task_type: 'local_bash', description: 'x' }] } }
      }),
      makeMessage({ type: 'system', content: 'BG bubble top-level', subtype: 'background_tasks_changed' }),
      makeMessage({ type: 'user', content: 'hello' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const texts = screen.getAllByRole('article').map(el => el.textContent)
    expect(texts).toContain('Claude Code Launched')
    expect(texts).toContain('hello')
    expect(texts.some(t => t.includes('BG bubble'))).toBe(false)
  })
})

// Issue #2035: three-way empty-state split (loading / error+retry / genuinely-empty),
// keyed off sessionStore.hydrationStageBySession.
describe('hydration-stage empty state (#2035)', () => {
  async function setup(hydrationEntry) {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: true,
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })
    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    if (hydrationEntry) {
      sessionStore.hydrationStageBySession.set(SESSION_ID, hydrationEntry)
    }
    await new Promise(r => setTimeout(r, 10))
    return { sessionStore }
  }

  it('shows a loading spinner + stage label while hydrating and no messages are loaded yet', async () => {
    await setup({ stage: 'loading_resources', error: null, controller: new AbortController() })

    expect(screen.getByText('Loading resources...')).toBeTruthy()
    expect(screen.queryByText('No messages yet. Start a conversation!')).toBeFalsy()
  })

  it('shows the error message + Retry button when hydration failed, and Retry calls retryHydration', async () => {
    const { sessionStore } = await setup({
      stage: 'error',
      error: { stage: 'loading_history', message: 'boom', kind: 'thrown' },
      controller: new AbortController(),
    })
    vi.spyOn(sessionStore, 'retryHydration').mockResolvedValue(undefined)

    expect(screen.getByText(/Failed to load session data \(loading_history\) — boom/)).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(sessionStore.retryHydration).toHaveBeenCalledWith(SESSION_ID)
  })

  it('shows the original "No messages yet" text unchanged once ready with genuinely no messages', async () => {
    await setup(null) // no hydrationStageBySession entry => hydrationStage defaults to 'ready'

    expect(screen.getByText('No messages yet. Start a conversation!')).toBeTruthy()
  })
})

// Issue #1746 (stage: layout): mergeConsecutiveAssistantTurns()
function makeMergeStub(capturedRuns) {
  return {
    template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>',
    props: ['message', 'attachedTools', 'orphanedPermissionTools', 'mergedMessages'],
    mounted() {
      capturedRuns.push({
        content: this.message.content,
        merged: (this.mergedMessages || []).map(m => m.content)
      })
    }
  }
}

describe('mergeConsecutiveAssistantTurns (#1746 stage: layout)', () => {
  it('merges 2+ consecutive assistant messages into the first item, rest as mergedMessages', async () => {
    const capturedRuns = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeMergeStub(capturedRuns), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'First turn' }),
      makeMessage({ type: 'assistant', content: 'Second turn' }),
      makeMessage({ type: 'assistant', content: 'Third turn' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(screen.getAllByRole('article').length).toBe(1)
    expect(capturedRuns[0].content).toBe('First turn')
    expect(capturedRuns[0].merged).toEqual(['Second turn', 'Third turn'])
  })

  it('does not merge across a user message', async () => {
    const capturedRuns = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeMergeStub(capturedRuns), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'First turn' }),
      makeMessage({ type: 'user', content: 'interjection' }),
      makeMessage({ type: 'assistant', content: 'Second turn' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(screen.getAllByRole('article').length).toBe(3)
    expect(capturedRuns.every(r => r.merged.length === 0)).toBe(true)
  })

  it('does not merge across a date separator', async () => {
    const capturedRuns = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeMergeStub(capturedRuns), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // 2 days apart (UTC) so the date-separator boundary is unambiguous regardless of local TZ.
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'Day one', timestamp: 1704067200 }),
      makeMessage({ type: 'assistant', content: 'Day three', timestamp: 1704240000 })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(screen.getAllByRole('article').length).toBe(2)
    expect(capturedRuns.every(r => r.merged.length === 0)).toBe(true)
  })

  it('does not merge past a message with a Task tool call, but that message may head/close a run', async () => {
    const capturedRuns = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeMergeStub(capturedRuns), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'Leading turn' }),
      makeMessage({
        type: 'assistant',
        content: 'Launching subagent',
        metadata: { has_tool_uses: true, tool_uses: [{ id: 'task-1', name: 'Task', input: {} }] }
      }),
      makeMessage({ type: 'assistant', content: 'After the task' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    // "Leading turn" + "Launching subagent" merge (the Task message may be the LAST member of
    // a run); "After the task" starts a new run since nothing may merge past a Task boundary.
    expect(screen.getAllByRole('article').length).toBe(2)
    expect(capturedRuns[0].content).toBe('Leading turn')
    expect(capturedRuns[0].merged).toEqual(['Launching subagent'])
    expect(capturedRuns[1].content).toBe('After the task')
    expect(capturedRuns[1].merged).toEqual([])
  })

  it('does not merge past a Task call that landed in attachedTools via groupToolsToParentMessages', async () => {
    const capturedRuns = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeMergeStub(capturedRuns), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // The Task tool_use lives on a content-less trailing message, which
    // groupToolsToParentMessages() consolidates into the PRECEDING message's attachedTools
    // (not its own metadata.tool_uses) — the boundary check must still catch it there.
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'Leading turn' }),
      makeMessage({
        type: 'assistant',
        content: '',
        metadata: { has_tool_uses: true, tool_uses: [{ id: 'task-1', name: 'Task', input: {} }] }
      }),
      makeMessage({ type: 'assistant', content: 'After the task' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(screen.getAllByRole('article').length).toBe(2)
    expect(capturedRuns[0].content).toBe('Leading turn')
    expect(capturedRuns[0].merged).toEqual([])
    expect(capturedRuns[1].content).toBe('After the task')
    expect(capturedRuns[1].merged).toEqual([])
  })

  it('does not merge past a send_comm tool call', async () => {
    const capturedRuns = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeMergeStub(capturedRuns), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'Sending comm',
        metadata: { has_tool_uses: true, tool_uses: [{ id: 'comm-1', name: 'mcp__legion__send_comm', input: {} }] }
      }),
      makeMessage({ type: 'assistant', content: 'After the comm' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(screen.getAllByRole('article').length).toBe(2)
    expect(capturedRuns.every(r => r.merged.length === 0)).toBe(true)
  })
})

// Helper stub capturing the two Issue #1957 continuation flags per rendered row.
function makeContinuationStub(capturedRows) {
  return {
    template: '<div role="article" data-testid="msg-item">{{ message.content }}</div>',
    props: ['message', 'attachedTools', 'orphanedPermissionTools', 'mergedMessages', 'isMessageIdContinuation', 'hasMessageIdContinuationFollowing'],
    mounted() {
      capturedRows.push({
        content: this.message.content,
        isMessageIdContinuation: !!this.isMessageIdContinuation,
        hasMessageIdContinuationFollowing: !!this.hasMessageIdContinuationFollowing,
      })
    }
  }
}

describe('markMessageIdContinuations — visual grouping (Issue #1957, follow-up to #1955)', () => {
  it('flags two independent rows sharing metadata.turn_id as a continuation, even with a subagent-terminal signal between them', async () => {
    const capturedRows = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeContinuationStub(capturedRows), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // One Anthropic turn (shared metadata.turn_id) split into two backend-persisted frames
    // — the #1765 background-Task-launch shape — with the subagent's own leg-terminal signal
    // landing chronologically between them (this is exactly why mergeConsecutiveAssistantTurns'
    // strict adjacency check does NOT merge them into one row).
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant', content: 'Launching a subagent', timestamp: 100,
        metadata: { turn_id: 'anthropic-turn-1', has_tool_uses: true, tool_uses: [{ id: 'launch-1', name: 'Task', input: {} }] }
      }),
      makeMessage({
        type: 'assistant', content: 'Second frame of the same turn', timestamp: 300,
        metadata: { turn_id: 'anthropic-turn-1', has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started',
      { task_id: 'task-1', tool_use_id: 'launch-1', description: 'Doing work' }, 100)
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_notification',
      { task_id: 'task-1', status: 'completed', summary: 'Done' }, 200)

    await new Promise(r => setTimeout(r, 50))

    // Both frames still render as their OWN independent row (never merged into mergedMessages).
    expect(capturedRows.length).toBe(2)
    expect(capturedRows[0].content).toBe('Launching a subagent')
    expect(capturedRows[0].isMessageIdContinuation).toBe(false)
    expect(capturedRows[0].hasMessageIdContinuationFollowing).toBe(true)
    expect(capturedRows[1].content).toBe('Second frame of the same turn')
    expect(capturedRows[1].isMessageIdContinuation).toBe(true)
    expect(capturedRows[1].hasMessageIdContinuationFollowing).toBe(false)
  })

  it('does not flag two frames with different metadata.turn_id, even with a signal between them', async () => {
    const capturedRows = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeContinuationStub(capturedRows), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant', content: 'First unrelated turn', timestamp: 100,
        metadata: { turn_id: 'anthropic-turn-A', has_tool_uses: true, tool_uses: [{ id: 'launch-1', name: 'Task', input: {} }] }
      }),
      makeMessage({
        type: 'assistant', content: 'Second unrelated turn', timestamp: 300,
        metadata: { turn_id: 'anthropic-turn-B', has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started',
      { task_id: 'task-1', tool_use_id: 'launch-1', description: 'Doing work' }, 100)
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_notification',
      { task_id: 'task-1', status: 'completed', summary: 'Done' }, 200)

    await new Promise(r => setTimeout(r, 50))

    expect(capturedRows.length).toBe(2)
    expect(capturedRows.every(r => !r.isMessageIdContinuation && !r.hasMessageIdContinuationFollowing)).toBe(true)
  })

  it('does not flag frames sharing metadata.turn_id when a genuine user message sits between them', async () => {
    const capturedRows = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: makeContinuationStub(capturedRows), TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'First frame', timestamp: 100, metadata: { turn_id: 'anthropic-turn-1' } }),
      makeMessage({ type: 'user', content: 'A real interjection', timestamp: 150 }),
      makeMessage({ type: 'assistant', content: 'Second frame', timestamp: 200, metadata: { turn_id: 'anthropic-turn-1' } })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const assistantRows = capturedRows.filter(r => r.content !== 'A real interjection')
    expect(assistantRows.length).toBe(2)
    expect(assistantRows.every(r => !r.isMessageIdContinuation && !r.hasMessageIdContinuationFollowing)).toBe(true)
  })

  it('renders the continuation row with the lighter turn-meta header instead of the full msg-meta (real AssistantMessage.vue, not stubbed)', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { TruncationBanner: true, SubagentTimeline: true, ActivityTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant', content: 'Launching a subagent', timestamp: 100,
        metadata: { turn_id: 'anthropic-turn-1', has_tool_uses: true, tool_uses: [{ id: 'launch-1', name: 'Task', input: {} }] }
      }),
      makeMessage({
        type: 'assistant', content: 'Second frame of the same turn', timestamp: 300,
        metadata: { turn_id: 'anthropic-turn-1', has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started',
      { task_id: 'task-1', tool_use_id: 'launch-1', description: 'Doing work' }, 100)
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_notification',
      { task_id: 'task-1', status: 'completed', summary: 'Done' }, 200)

    await new Promise(r => setTimeout(r, 50))

    const bubbles = document.querySelectorAll('[data-testid="assistant-message"]')
    expect(bubbles.length).toBe(2)

    // First frame: normal full header, no continuation classes.
    expect(bubbles[0].classList.contains('is-message-id-continuation')).toBe(false)
    expect(bubbles[0].classList.contains('has-message-id-continuation-following')).toBe(true)
    expect(bubbles[0].querySelector('.msg-meta')).toBeTruthy()

    // Second frame: continuation — no full header, lighter turn-meta instead.
    expect(bubbles[1].classList.contains('is-message-id-continuation')).toBe(true)
    expect(bubbles[1].querySelector('.msg-meta')).toBeNull()
    expect(bubbles[1].querySelector('.turn-meta')).toBeTruthy()
  })
})

// Helper stub that exposes orphanedPermissionTools count as a data attribute
function makeMessageItemStub(capturedOrphans) {
  return {
    template: '<div role="article" :data-orphaned="(orphanedPermissionTools || []).length" :data-orphaned-ids="(orphanedPermissionTools || []).map(t => t.id).join(\',\')"></div>',
    props: ['message', 'attachedTools', 'orphanedPermissionTools'],
    mounted() {
      capturedOrphans.push(...(this.orphanedPermissionTools || []))
    }
  }
}

describe('attachOrphanedPermissionTools — Fix B (#1626)', () => {
  it('attaches permission_required tool to last assistant when metadata.tool_uses is empty', async () => {
    const capturedOrphans = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: makeMessageItemStub(capturedOrphans),
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // Assistant message with NO tool_uses in metadata
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'I will edit the file.',
        metadata: { has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // Tool in store with permission_required status (backendStatus: awaiting_permission)
    messageStore.toolCallsBySession.set(SESSION_ID, [{
      id: 'tool-perm-1',
      name: 'Edit',
      input: { file_path: '/tmp/foo.txt' },
      status: 'permission_required',
      backendStatus: 'awaiting_permission'
    }])
    messageStore.toolCallsBySession = new Map(messageStore.toolCallsBySession)

    await new Promise(r => setTimeout(r, 50))

    // The tool should be attached as an orphaned permission tool
    expect(capturedOrphans.length).toBeGreaterThanOrEqual(1)
    expect(capturedOrphans.some(t => t.id === 'tool-perm-1')).toBe(true)
  })

  it('does not attach tool already present in message metadata.tool_uses', async () => {
    const capturedOrphans = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: makeMessageItemStub(capturedOrphans),
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // Assistant message WITH tool_uses already in metadata
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'I will edit the file.',
        metadata: {
          has_tool_uses: true,
          tool_uses: [{ id: 'tool-known-1', name: 'Edit', input: {} }]
        }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // Same tool in store with permission_required status
    messageStore.toolCallsBySession.set(SESSION_ID, [{
      id: 'tool-known-1',
      name: 'Edit',
      input: {},
      status: 'permission_required',
      backendStatus: 'awaiting_permission'
    }])
    messageStore.toolCallsBySession = new Map(messageStore.toolCallsBySession)

    await new Promise(r => setTimeout(r, 50))

    // Tool is already referenced in metadata — must NOT be duplicated as orphan
    expect(capturedOrphans.filter(t => t.id === 'tool-known-1').length).toBe(0)
  })

  it('does not attach non-permission tools (e.g. running or completed)', async () => {
    const capturedOrphans = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: makeMessageItemStub(capturedOrphans),
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'Running bash.',
        metadata: { has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // Tool is running (not permission_required)
    messageStore.toolCallsBySession.set(SESSION_ID, [{
      id: 'tool-running-1',
      name: 'Bash',
      input: { command: 'ls' },
      status: 'executing',
      backendStatus: 'running'
    }])
    messageStore.toolCallsBySession = new Map(messageStore.toolCallsBySession)

    await new Promise(r => setTimeout(r, 50))

    // Running tool must NOT be attached as orphaned permission tool
    expect(capturedOrphans.filter(t => t.id === 'tool-running-1').length).toBe(0)
  })

  it('anchors by messageId to an earlier bubble, not the last one (#1694)', async () => {
    const capturedOrphans = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: makeMessageItemStub(capturedOrphans),
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // Two assistant bubbles, separated by a user interjection so mergeConsecutiveAssistantTurns()
    // (#1746) doesn't fold them into one item — this test needs two separate top-level bubbles.
    // The orphaned tool's messageId matches the EARLIER one via metadata.turn_id — the
    // TURN-level identity ToolCall.turn_id is sourced from (Issue #1957: the top-level
    // message_id is now the PER-FRAME identity #1955's dedup needs, a different value).
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'First turn — requests permission',
        message_id: 'frame-uuid-early',
        metadata: { turn_id: 'msg-early', has_tool_uses: false, tool_uses: [] }
      }),
      makeMessage({ type: 'user', content: 'interjection' }),
      makeMessage({
        type: 'assistant',
        content: 'Second, unrelated turn',
        message_id: 'frame-uuid-late',
        metadata: { turn_id: 'msg-late', has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    messageStore.toolCallsBySession.set(SESSION_ID, [{
      id: 'tool-perm-early',
      name: 'Edit',
      input: { file_path: '/tmp/foo.txt' },
      status: 'permission_required',
      backendStatus: 'awaiting_permission',
      messageId: 'msg-early'
    }])
    messageStore.toolCallsBySession = new Map(messageStore.toolCallsBySession)

    await new Promise(r => setTimeout(r, 50))

    const items = screen.getAllByRole('article')
    // Recency-based fallback would have attached to the LAST bubble (index 2).
    expect(items[0].getAttribute('data-orphaned-ids')).toBe('tool-perm-early')
    expect(items[2].getAttribute('data-orphaned-ids')).toBe('')
  })

  it('falls back to the last-bubble heuristic when messageId is absent (#1694)', async () => {
    const capturedOrphans = []
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: makeMessageItemStub(capturedOrphans),
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // User interjection between the two assistant turns so mergeConsecutiveAssistantTurns()
    // (#1746) doesn't fold them into one item — the last-bubble fallback needs two bubbles.
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'First turn',
        message_id: 'msg-early',
        metadata: { has_tool_uses: false, tool_uses: [] }
      }),
      makeMessage({ type: 'user', content: 'interjection' }),
      makeMessage({
        type: 'assistant',
        content: 'Second turn — the one requesting permission',
        message_id: 'msg-late',
        metadata: { has_tool_uses: false, tool_uses: [] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // No messageId on the tool — legacy stored data.
    messageStore.toolCallsBySession.set(SESSION_ID, [{
      id: 'tool-perm-legacy',
      name: 'Edit',
      input: { file_path: '/tmp/foo.txt' },
      status: 'permission_required',
      backendStatus: 'awaiting_permission'
    }])
    messageStore.toolCallsBySession = new Map(messageStore.toolCallsBySession)

    await new Promise(r => setTimeout(r, 50))

    const items = screen.getAllByRole('article')
    expect(items[0].getAttribute('data-orphaned-ids')).toBe('')
    expect(items[2].getAttribute('data-orphaned-ids')).toBe('tool-perm-legacy')
  })
})

// Issue #1748 (stage: offset-model)
const MESSAGE_ITEM_STUB = {
  template: '<div role="article">{{ message.content }}</div>',
  props: ['message', 'attachedTools', 'orphanedPermissionTools', 'mergedMessages']
}

describe('virtualizer offset model (#1748 stage: offset-model)', () => {
  it('gutter lanes resolve via the virtualizer offset model — a still-running leg spans further than a completed one', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant', content: 'Launching a subagent that keeps running', timestamp: 100,
        metadata: { has_tool_uses: true, tool_uses: [{ id: 'launch-running', name: 'Task', input: {} }] }
      }),
      makeMessage({ type: 'user', content: 'meanwhile, unrelated turns happen', timestamp: 150 }),
      makeMessage({
        type: 'assistant', content: 'Launching a subagent that finishes quickly', timestamp: 200,
        metadata: { has_tool_uses: true, tool_uses: [{ id: 'launch-done', name: 'Task', input: {} }] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // One leg stays running (its lane must extend to "wherever the conversation currently is" —
    // i.e. the virtualizer's total size); the other reaches a terminal status (its lane is
    // bounded by its own terminal row's offset) — see MessageList.vue's `laneOffsets` computed.
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started',
      { task_id: 'task-running', tool_use_id: 'launch-running', description: 'Still going' }, 100)
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started',
      { task_id: 'task-done', tool_use_id: 'launch-done', description: 'Finished' }, 200)
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_notification',
      { task_id: 'task-done', status: 'completed', summary: 'All done' }, 250)

    await new Promise(r => setTimeout(r, 50))

    // Both legs' lanes rendering at all proves toolUseIndexMap/signalIndexMap resolved a real
    // displayableItems index for each — a lane silently disappears (matching the old
    // `if (!startEl) continue` behavior) whenever that resolution fails.
    const lanes = document.querySelectorAll('.gutter-lane')
    expect(lanes.length).toBe(2)

    const heights = Array.from(lanes).map(l => parseFloat(l.style.height))
    expect(heights.every(h => Number.isFinite(h) && h > 0)).toBe(true)
    expect(Math.max(...heights)).toBeGreaterThan(Math.min(...heights))
  })

  // Issue #1748 follow-up (user feedback): a gutter chip must align to the actual launch/
  // terminal anchor's own position, not the top/bottom of the whole row/block it lives in — a
  // long completion summary below the terminal header must not stretch the lane's bottom.
  it("gutter lane bottom tracks the terminal anchor's own DOM position, not its row's full extent (including a long result body)", async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant', content: 'Launching', timestamp: 100,
        metadata: { has_tool_uses: true, tool_uses: [{ id: 'launch-1', name: 'Task', input: {} }] }
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started',
      { task_id: 'task-1', tool_use_id: 'launch-1', description: 'Task' }, 100)
    messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_notification',
      { task_id: 'task-1', status: 'completed', summary: 'A long completion summary' }, 200)

    await new Promise(r => setTimeout(r, 50))

    const terminalRow = document.querySelector('[id^="subagent-anchor-terminal-"]')
    expect(terminalRow).toBeTruthy()
    const rowWrapper = terminalRow.closest('.virtual-item-row')
    expect(rowWrapper).toBeTruthy()

    // The terminal header sits near the top of its row; the row itself is much taller because
    // of a long markdown result body rendered below the header (anchor-body, per
    // SubagentAnchorRow.vue). Instance-level spies (not prototype-wide) so only these two real
    // elements are affected.
    vi.spyOn(rowWrapper, 'getBoundingClientRect').mockReturnValue({ top: 1000, bottom: 1400 })
    vi.spyOn(terminalRow, 'getBoundingClientRect').mockReturnValue({ top: 1010, bottom: 1036 })

    // laneOffsets is a Vue computed — force it to re-run by touching one of its reactive
    // dependencies (indexMaps, via displayableItems) now that the spies are installed.
    messageStore.messagesBySession.set(SESSION_ID, [
      ...messageStore.messagesBySession.get(SESSION_ID),
      makeMessage({ type: 'user', content: 'one more turn', timestamp: 300 })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    const lane = document.querySelector('.gutter-lane')
    expect(lane).toBeTruthy()
    const top = parseFloat(lane.style.top)
    const height = parseFloat(lane.style.height)
    // Issue #1911: a leading date-separator row now precedes the launch row (both messages fall
    // on the same calendar day), adding one more unmocked row at the ResizeObserver stub's
    // default height (600) ahead of it. The launch row (index 1, unmocked) then measures at that
    // same default height — together the two unmocked rows become the terminal row's own start
    // offset in the virtualizer's coordinate system. bottom = that start + the anchor's own
    // offset within its row (1036 - 1000 = 36), NOT the row's full bottom (1400 - 1000 = 400).
    expect(top + height).toBeCloseTo(600 + 600 + 36, 0)
  })

  it('scrolls to the new bottom when a message is appended while sticky-to-bottom (§7)', async () => {
    const scrollToSpy = vi.fn()
    // jsdom has no native Element.prototype.scrollTo — the virtualizer's elementScroll calls
    // scrollElement.scrollTo({top, behavior}) as its scroll mechanism.
    Element.prototype.scrollTo = scrollToSpy

    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const { useUIStore } = await import('@/stores/ui')
    const messageStore = useMessageStore(pinia)
    useUIStore(pinia).autoScrollEnabled = true

    messageStore.messagesBySession.set(SESSION_ID, [makeMessage({ type: 'assistant', content: 'First' })])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))
    scrollToSpy.mockClear()

    // 'user' (not 'assistant') so mergeConsecutiveAssistantTurns() doesn't fold it into the
    // first item — this test is about the item COUNT changing, not merge behavior.
    messageStore.messagesBySession.set(SESSION_ID, [
      ...messageStore.messagesBySession.get(SESSION_ID),
      makeMessage({ type: 'user', content: 'Second' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // scheduleStickyScroll coalesces via nextTick + requestAnimationFrame (§7/§10).
    await new Promise(r => setTimeout(r, 50))

    expect(scrollToSpy).toHaveBeenCalled()
  })

  it('re-pins to bottom when the streaming preview\'s content grows — replaces the old tracked-row streaming-growth trigger (Issue #1955)', async () => {
    const scrollToSpy = vi.fn()
    Element.prototype.scrollTo = scrollToSpy

    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const { useUIStore } = await import('@/stores/ui')
    const messageStore = useMessageStore(pinia)
    useUIStore(pinia).autoScrollEnabled = true

    messageStore.messagesBySession.set(SESSION_ID, [makeMessage({ type: 'assistant', content: 'First' })])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))
    scrollToSpy.mockClear()

    // Issue #1955: the live-typing preview renders OUTSIDE the virtualizer's tracked rows (a
    // normal-flow sibling, like TruncationBanner) — its growth no longer trips the virtualizer's
    // own onChange (which only fires for tracked-row measurement changes). The dedicated
    // content-length watch in MessageList.vue is the replacement trigger.
    messageStore.handleAssistantDelta(SESSION_ID, { uuid: 'env-1', event: { type: 'message_start', message: { id: 'msg-stream-1' } } })
    messageStore.handleAssistantDelta(SESSION_ID, {
      uuid: 'env-2',
      event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'Streaming in more text' } }
    })

    // Let the rAF-batched preview flush run, then the component's own nextTick+rAF-coalesced
    // scheduleStickyScroll (§7/§10).
    await new Promise(resolve => requestAnimationFrame(resolve))
    await new Promise(r => setTimeout(r, 50))

    expect(scrollToSpy).toHaveBeenCalled()
  })

  it('StreamingPreview renders as a sibling after the virtual spacer and is not counted among the virtualizer\'s tracked rows (Issue #1955)', async () => {
    const { pinia } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [makeMessage({ type: 'assistant', content: 'First' })])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    const rowCountBefore = document.querySelectorAll('.virtual-item-row').length

    // Text only (no thinking) keeps this test from also exercising ThinkingBlock — irrelevant
    // to what's being asserted here (sibling positioning, not counted as a tracked row).
    messageStore.handleAssistantDelta(SESSION_ID, { uuid: 'env-1', event: { type: 'message_start', message: { id: 'msg-preview-1' } } })
    messageStore.handleAssistantDelta(SESSION_ID, {
      uuid: 'env-2',
      event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'typing...' } }
    })
    await new Promise(resolve => requestAnimationFrame(resolve))
    await new Promise(r => setTimeout(r, 20))

    const preview = document.querySelector('[data-testid="streaming-preview"]')
    expect(preview).toBeTruthy()
    expect(preview.closest('.virtual-item-row')).toBeNull()
    expect(document.querySelectorAll('.virtual-item-row').length).toBe(rowCountBefore)
  })

  // Issue #1748 (stage: windowing) regression: a real overscan value means rowVirtualizer's
  // onChange (scheduleStickyScroll's other trigger, alongside item-count/tool-call watchers) now
  // fires on nearly every scroll tick that crosses a mounted row's boundary — not just on
  // genuine content growth, as it effectively only did at stage 1's overscan=count. Reported by
  // the user as "scroll up near the bottom and it snaps back down, repeatedly, unless you scroll
  // fast": scheduleStickyScroll's deferred (nextTick+rAF) callback used to act on a `true`
  // stickyToBottom read back at schedule time, with no re-check before actually force-scrolling —
  // if the user's own onScroll handler hadn't yet flipped it to `false` by the time the deferred
  // callback ran, it would snap back to bottom regardless of where the user had actually scrolled.
  it('does not snap back to bottom if the user scrolled away before the deferred sticky-scroll fires', async () => {
    const scrollToSpy = vi.fn()
    Element.prototype.scrollTo = scrollToSpy

    const { pinia, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const { useUIStore } = await import('@/stores/ui')
    const messageStore = useMessageStore(pinia)
    useUIStore(pinia).autoScrollEnabled = true

    messageStore.messagesBySession.set(SESSION_ID, [makeMessage({ type: 'assistant', content: 'First' })])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))
    scrollToSpy.mockClear()

    const el = container.querySelector('.messages-area')
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 5000 })
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 600 })

    // Triggers scheduleStickyScroll while stickyToBottom is still true (matches an onChange
    // firing from a scroll-driven range change, or — as here — a real content-growth trigger).
    messageStore.messagesBySession.set(SESSION_ID, [
      ...messageStore.messagesBySession.get(SESSION_ID),
      makeMessage({ type: 'user', content: 'Second' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    // Before the deferred nextTick+rAF callback runs, the user scrolls away from the bottom.
    // Dispatching a real 'scroll' event (not just writing scrollTop) matters: the fix relies on
    // this component's own onScroll handler — a synchronous listener for that same event — having
    // already corrected `stickyToBottom` to false by the time the deferred callback checks it.
    // Issue #2100: a bare 'scroll' event is no longer trusted as user-caused on its own — a
    // preceding 'wheel' event simulates the genuine user-scroll gesture this test means to
    // exercise.
    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = 1000
    el.dispatchEvent(new Event('scroll'))

    await new Promise(r => setTimeout(r, 50))

    // Not a blanket "never called" — the virtualizer's own settle-reconciliation mechanism
    // (correcting an earlier scrollToIndex call's estimated offset once real measurements land)
    // can legitimately call scrollTo with a small, unrelated offset. The actual regression is
    // specifically a jump BACK TO THE BOTTOM (maxOffset here is 5000 - 600 = 4400) — assert none
    // of the calls target anywhere near that.
    const calls = scrollToSpy.mock.calls.map(([opts]) => opts?.top).filter(top => typeof top === 'number')
    expect(calls.every(top => top < 3000)).toBe(true)
  })

  // Issue #1748 (stage: windowing) regression: the first attempt at the fix above re-checked raw
  // DOM scroll geometry instead of the `stickyToBottom` ref, which broke the very first
  // scroll-to-bottom on initial load — scrollTop is legitimately still 0 (nothing has scrolled
  // down yet) while scrollHeight already reflects the virtualizer's estimated total size, so a
  // DOM-only check can't distinguish "never scrolled down yet" from "user scrolled away."
  it('still scrolls to the bottom once messages arrive asynchronously after mount, even though scrollTop starts at 0 (regression)', async () => {
    const scrollToSpy = vi.fn()
    Element.prototype.scrollTo = scrollToSpy

    const { pinia, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useUIStore } = await import('@/stores/ui')
    useUIStore(pinia).autoScrollEnabled = true

    // Let the component fully mount and settle with NO messages yet (matches a real page
    // load/refresh: the message-history API call is still in flight when MessageList mounts).
    // This is important — mounting inside a <KeepAlive> also fires onActivated, which makes its
    // own direct (unconditional) scrollToBottomViaVirtualizer() call independent of this fix's
    // code path; waiting it out here first, on an empty list, isolates the actual regression:
    // the watcher-triggered scheduleStickyScroll() path that fires once messages actually load.
    await new Promise(r => setTimeout(r, 50))
    scrollToSpy.mockClear()

    const el = container.querySelector('.messages-area')
    // A large already-estimated total size, exactly like a freshly-mounted virtualizer reports
    // for a large session before anything has actually been measured — while scrollTop is still
    // untouched (0), matching first paint before any scroll-to-bottom has happened yet.
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 1900000 })
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 600 })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)
    messageStore.messagesBySession.set(SESSION_ID, [makeMessage({ type: 'assistant', content: 'First' })])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(scrollToSpy).toHaveBeenCalled()
  })
})

// Issue #2100: autoscroll must only disable on a genuine user scroll gesture (wheel, touch,
// keyboard, or scrollbar drag) — not on a bare native 'scroll' event with no user input, which
// browser scroll anchoring can fire on its own when the virtualizer corrects a row's estimated
// height to its real rendered size.
describe('gesture-gated autoscroll (#2100)', () => {
  async function setupGestureTest() {
    const { pinia, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })
    const { useMessageStore } = await import('@/stores/message')
    const { useUIStore } = await import('@/stores/ui')
    const messageStore = useMessageStore(pinia)
    const uiStore = useUIStore(pinia)
    uiStore.autoScrollEnabled = true

    messageStore.messagesBySession.set(SESSION_ID, [makeMessage({ type: 'assistant', content: 'First' })])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    const el = container.querySelector('.messages-area')
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 5000 })
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 600 })

    return { pinia, container, el, uiStore, messageStore }
  }

  const FAR_SCROLL_TOP = 1000        // distance = 5000-1000-600 = 3400, past STICKY_THRESHOLD_PX
  const NEAR_BOTTOM_SCROLL_TOP = 4980 // distance = 5000-4980-600 = -580, under STICKY_THRESHOLD_PX

  it('a bare scroll event with no preceding gesture does not disable stickyToBottom (scroll-anchoring regression)', async () => {
    const { el, uiStore } = await setupGestureTest()

    // Establish a known sticky=true baseline via a real gesture first — a plain mount in this
    // test harness doesn't itself populate uiStore's per-session map (that only happens via a
    // gated scroll recompute, or a few other explicit call sites not exercised here).
    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = NEAR_BOTTOM_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)

    // Let that gesture's window fully expire — a scroll within it is legitimately still trusted
    // by design (the window is time-based, not per-event-causal), so the "bare" scroll under
    // test must land after it closes to actually exercise the no-gesture path.
    await new Promise(r => setTimeout(r, 300))

    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))

    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)
  })

  it('wheel followed by scroll disables stickyToBottom', async () => {
    const { el, uiStore } = await setupGestureTest()

    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))

    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(false)
  })

  it('touchstart followed by touchmove then scroll disables stickyToBottom', async () => {
    const { el, uiStore } = await setupGestureTest()

    el.dispatchEvent(new Event('touchstart', { bubbles: true }))
    el.dispatchEvent(new Event('touchmove', { bubbles: true }))
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))

    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(false)
  })

  it('a plain tap (touchstart+touchend with no touchmove) does not open a momentum window for a later unrelated scroll', async () => {
    const { el, uiStore } = await setupGestureTest()

    // Establish a known sticky=true baseline via a real gesture first (see the "bare scroll"
    // test above for why this is needed in this harness).
    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = NEAR_BOTTOM_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)
    await new Promise(r => setTimeout(r, 300))

    el.dispatchEvent(new Event('touchstart', { bubbles: true }))
    el.dispatchEvent(new Event('touchend', { bubbles: true }))

    // Past GESTURE_WINDOW_MS but still well under TOUCH_MOMENTUM_CAP_MS — if the tap had
    // incorrectly opened a momentum window (no movement ever happened, so there's no momentum
    // to track), this later bare scroll would still be wrongly trusted.
    await new Promise(r => setTimeout(r, 300))

    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))

    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)
  })

  it('touch momentum scroll arriving after touchend, within the hard cap, still disables stickyToBottom', async () => {
    const { el, uiStore } = await setupGestureTest()

    el.dispatchEvent(new Event('touchstart', { bubbles: true }))
    el.dispatchEvent(new Event('touchmove', { bubbles: true }))
    el.dispatchEvent(new Event('touchend', { bubbles: true }))

    // Past GESTURE_WINDOW_MS (250ms) but well under TOUCH_MOMENTUM_CAP_MS (2000ms) — simulates
    // native momentum still decelerating after the finger has lifted.
    await new Promise(r => setTimeout(r, 300))

    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))

    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(false)
  })

  it('ArrowUp keydown on window then scroll disables stickyToBottom; the same keydown while focus is in a textarea does not', async () => {
    const { el, uiStore } = await setupGestureTest()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }))
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(false)

    // Re-enable via a genuine gesture before testing the InputArea exclusion in isolation.
    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = NEAR_BOTTOM_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)

    // Let that gesture's window fully expire so it can't bleed into the exclusion check below.
    await new Promise(r => setTimeout(r, 300))

    // InputArea.vue is a sibling component sharing the page — typing ArrowUp in its textarea
    // must not open a gesture window.
    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)
    textarea.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }))
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)
    document.body.removeChild(textarea)
  })

  it('mousedown then scroll disables stickyToBottom (scrollbar drag); the window closes shortly after mouseup', async () => {
    const { el, uiStore } = await setupGestureTest()

    el.dispatchEvent(new Event('mousedown', { bubbles: true }))
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(false)

    // Still held — scrolling back near the bottom via the same drag re-enables stickiness.
    el.scrollTop = NEAR_BOTTOM_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)

    el.dispatchEvent(new Event('mouseup', { bubbles: true }))
    await new Promise(r => setTimeout(r, 300)) // past GESTURE_WINDOW_MS

    // Window has closed — a later bare scroll away from the bottom must not disable stickiness.
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)
  })

  it('scrolling back down near the bottom after a real gesture still re-enables stickyToBottom', async () => {
    const { el, uiStore } = await setupGestureTest()

    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = FAR_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(false)

    el.dispatchEvent(new Event('wheel', { bubbles: true }))
    el.scrollTop = NEAR_BOTTOM_SCROLL_TOP
    el.dispatchEvent(new Event('scroll'))
    expect(uiStore.stickyToBottomBySession.get(SESSION_ID)).toBe(true)
  })
})

// Issue #1848: findPrevTurnIndex/findNextTurnIndex — turn-boundary jump navigation exposed for
// SessionStatusBar's prev/next buttons. A "turn boundary" is any displayableItems entry whose
// message is user-authored, which covers both real user messages and inbound comms (comms are
// delivered as user-role messages carrying metadata.comm — see UserMessage.vue's isComm check).
describe('findPrevTurnIndex / findNextTurnIndex (#1848)', () => {
  function stubScrollMechanics(scrollEl) {
    Element.prototype.scrollTo = function (opts) {
      const top = typeof opts === 'object' && opts !== null ? opts.top : undefined
      if (typeof top === 'number') {
        this.scrollTop = top
        this.dispatchEvent(new Event('scroll'))
      }
    }
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, get: () => 100000 })
    Object.defineProperty(scrollEl, 'clientHeight', { configurable: true, get: () => 600 })
  }

  it('finds the nearest user/comm boundary above and below the current visible range', async () => {
    const { pinia, wrapper, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // Issue #1911: a leading date-separator row now precedes index 0 (all messages fall on the
    // same calendar day), shifting every index below by one: index 0: the new leading separator,
    // index 1: user (boundary A), 2-50: system filler (type 'system' so each stays its own
    // displayableItems entry — unlike 'assistant', it neither merges via
    // mergeConsecutiveAssistantTurns nor counts as a turn boundary itself), index 51: inbound
    // comm (boundary B, user-role), 52-100: system filler. Boundaries are spaced far enough apart
    // (50 rows) that scrolling to the midpoint keeps both OUTSIDE the virtualizer's mounted
    // range (visible rows + OVERSCAN_ROWS on each side, ~24 rows total) — visibleIndexRange
    // reflects the current MOUNTED window, not just the pixel-visible viewport, so a small gap
    // would leave both boundaries still "mounted" and make this test meaningless.
    const messages = [
      makeMessage({ id: 'msg-0', message_id: 'msg-0', type: 'user', content: 'First user message', timestamp: 1700000000 }),
      ...Array.from({ length: 49 }, (_, i) =>
        makeMessage({ id: `msg-a${i}`, message_id: `msg-a${i}`, type: 'system', content: `Filler ${i}`, timestamp: 1700000001 + i })
      ),
      makeMessage({
        id: 'msg-comm', message_id: 'msg-comm', type: 'user', content: 'Inbound comm', timestamp: 1700000050,
        metadata: { comm: { from_name: 'minion-a', from_display_name: 'Minion A' } }
      }),
      ...Array.from({ length: 49 }, (_, i) =>
        makeMessage({ id: `msg-b${i}`, message_id: `msg-b${i}`, type: 'system', content: `Filler ${i}`, timestamp: 1700000051 + i })
      )
    ]
    messageStore.messagesBySession.set(SESSION_ID, messages)
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    stubScrollMechanics(container.querySelector('.messages-area'))

    // Scroll to the midpoint (index 25) — equidistant from both boundaries, well outside the
    // mounted window in either direction.
    await wrapper.vm.scrollToItemIndex(25, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 20))

    expect(wrapper.vm.findPrevTurnIndex()).toBe(1)
    expect(wrapper.vm.findNextTurnIndex()).toBe(51)
  })

  it('returns null when there is no boundary above/below (first/last message in view)', async () => {
    const { pinia, wrapper, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    // Single user message — no other boundary to jump to in either direction.
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'user', content: 'Only message' })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    stubScrollMechanics(container.querySelector('.messages-area'))

    expect(wrapper.vm.findPrevTurnIndex()).toBeNull()
    expect(wrapper.vm.findNextTurnIndex()).toBeNull()
  })
})

// Issue #1748 (stage: windowing): overscan is now a real, small value (OVERSCAN_ROWS) instead of
// the full item count — these tests exercise the actual culling/remount/jump behavior that stage
// 1 deliberately kept dormant (see MessageList.vue's virtualizerOptions comment).
describe('windowing — real overscan (#1748 stage: windowing)', () => {
  function manyMessages(count) {
    return Array.from({ length: count }, (_, i) =>
      makeMessage({ id: `msg-${i}`, message_id: `msg-${i}`, type: 'user', content: `Message ${i}`, timestamp: 1700000000 + i })
    )
  }

  // jsdom's real Element.scrollTo is a no-op (doesn't move scrollTop or fire 'scroll') — the
  // virtualizer has no optimistic internal offset update of its own, it learns the new scroll
  // position exclusively via a native 'scroll' event listener (see @tanstack/virtual-core's
  // observeElementOffset). Without this, scrollToIndex()/scrollToOffset() would never actually
  // change what's mounted under jsdom, and these tests would be unable to exercise real culling.
  //
  // Separately, @tanstack/virtual-core's getMaxScrollOffset() clamps every scroll target against
  // the scroll element's REAL `scrollHeight`/`clientHeight` (not its own ResizeObserver-tracked
  // size) — jsdom has no layout engine, so both are always 0, which would clamp every jump to
  // offset 0 regardless of target. Stubbing them on the actual scroll element is required for any
  // scrollToIndex/scrollToOffset call to have an observable effect under jsdom.
  function stubScrollMechanics(scrollEl) {
    Element.prototype.scrollTo = function (opts) {
      const top = typeof opts === 'object' && opts !== null ? opts.top : undefined
      if (typeof top === 'number') {
        this.scrollTop = top
        this.dispatchEvent(new Event('scroll'))
      }
    }
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, get: () => 100000 })
    Object.defineProperty(scrollEl, 'clientHeight', { configurable: true, get: () => 600 })
  }

  it('unmounts rows far outside the overscan window and remounts them when scrolled back', async () => {
    const { pinia, wrapper, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, manyMessages(40))
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    // Initial mount (scrolled to top) includes index 0.
    expect(document.querySelector('[data-index="0"]')).toBeTruthy()

    stubScrollMechanics(container.querySelector('.messages-area'))

    // Jump far away — index 0 must fall well outside [visible ± OVERSCAN_ROWS] and unmount.
    await wrapper.vm.scrollToItemIndex(35, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 20))
    expect(document.querySelector('[data-index="0"]')).toBeFalsy()

    // Jump back — index 0 must remount.
    await wrapper.vm.scrollToItemIndex(0, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 20))
    expect(document.querySelector('[data-index="0"]')).toBeTruthy()
  })

  it('scrollToItemIndex mounts a far, previously-unmounted target (two-phase jump pattern, §5.4/§5.5)', async () => {
    const { pinia, wrapper, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, manyMessages(40))
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    expect(document.querySelector('[data-index="30"]')).toBeFalsy()

    stubScrollMechanics(container.querySelector('.messages-area'))
    const mounted = await wrapper.vm.scrollToItemIndex(30, { align: 'auto', behavior: 'auto' })

    expect(mounted).toBe(true)
    expect(document.querySelector('[data-index="30"]')).toBeTruthy()
  })

  // Issue #1748 (stage: windowing) local-state audit: ActivityTimeline's expanded-tool-card
  // state is store-backed (message.js: getExpandedTimelineTool/setExpandedTimelineTool) so it
  // survives a row genuinely unmounting and remounting with a fresh setup() call — the primary
  // risk this stage's audit was required to address (see MessageList.vue's builder context).
  it('preserves ActivityTimeline expand state across a real unmount/remount cycle', async () => {
    const messages = manyMessages(40)
    messages[0] = makeMessage({
      id: 'msg-with-tool',
      message_id: 'msg-with-tool',
      type: 'assistant',
      content: '',
      timestamp: 1700000000,
      metadata: {
        has_tool_uses: true,
        tool_uses: [{ id: 'tool-bash-1', name: 'Bash', input: { command: 'ls' } }]
      }
    })

    const { pinia, wrapper, container } = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        TimelineDetail: { template: '<div data-testid="timeline-detail" :data-tool-id="toolCall.id" />', props: ['toolCall'] },
        PermissionPrompt: true,
        SubagentTimeline: true,
        TruncationBanner: true
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, messages)
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    messageStore.toolCallsBySession.set(SESSION_ID, [
      { id: 'tool-bash-1', name: 'Bash', input: { command: 'ls' }, status: 'completed', result: { content: 'ok' } }
    ])
    messageStore.toolCallsBySession = new Map(messageStore.toolCallsBySession)
    await new Promise(r => setTimeout(r, 50))

    const node = document.querySelector('[data-testid="timeline-node"]')
    expect(node).toBeTruthy()
    node.dispatchEvent(new Event('click', { bubbles: true }))
    await new Promise(r => setTimeout(r, 10))

    expect(document.querySelector('[data-testid="timeline-detail"]')).toBeTruthy()

    stubScrollMechanics(container.querySelector('.messages-area'))

    // Scroll far away — the row (and its ActivityTimeline instance) genuinely unmounts.
    await wrapper.vm.scrollToItemIndex(35, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 20))
    expect(document.querySelector('[data-index="0"]')).toBeFalsy()

    // Scroll back — a fresh ActivityTimeline instance mounts; without store-backing this would
    // read local expandedNodeId = null and show collapsed. It must come back expanded.
    await wrapper.vm.scrollToItemIndex(0, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 20))

    expect(document.querySelector('[data-testid="timeline-detail"]')?.getAttribute('data-tool-id')).toBe('tool-bash-1')
  })

  // Issue #1748 (stage: windowing) — task: re-verify scroll-position save/restore actually
  // fires correctly now that rows genuinely unmount/remount, not just assumed correct because
  // stage 1 wired it up. onActivated/onDeactivated (where save/restore live, §8) only ever fire
  // for a component inside a real <KeepAlive> — every other test in this file mounts MessageList
  // directly, so this is the only test that exercises that lifecycle at all.
  it('saves and restores scroll position across a KeepAlive deactivate/reactivate cycle (§8)', async () => {
    const { defineComponent, h, KeepAlive } = await import('vue')
    let exposedMessageList = null
    const Host = defineComponent({
      props: { show: { type: Boolean, default: true } },
      render() {
        return h(KeepAlive, null, {
          default: () => (this.show
            ? h(MessageList, { ref: el => { if (el) exposedMessageList = el } })
            : h('div', { 'data-testid': 'placeholder' }))
        })
      }
    })

    const { pinia, wrapper, container } = renderWithStores(Host, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true }
    })

    const { useMessageStore } = await import('@/stores/message')
    const { useUIStore } = await import('@/stores/ui')
    const messageStore = useMessageStore(pinia)
    // Restore (not scroll-to-bottom) is the branch under test — matches a user who scrolled up
    // into history before switching sessions.
    useUIStore(pinia).autoScrollEnabled = false

    messageStore.messagesBySession.set(SESSION_ID, manyMessages(40))
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)
    await new Promise(r => setTimeout(r, 50))

    stubScrollMechanics(container.querySelector('.messages-area'))
    await exposedMessageList.scrollToItemIndex(20, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 20))
    expect(document.querySelector('[data-index="20"]')).toBeTruthy()

    // Deactivate: onDeactivated must save the current top-most-visible position.
    await wrapper.setProps({ show: false })
    await new Promise(r => setTimeout(r, 20))
    const { useSessionStore } = await import('@/stores/session')
    const saved = useSessionStore(pinia).scrollPositions.get(SESSION_ID)
    expect(saved).toBeTruthy()
    expect(saved.itemIndex).toBeGreaterThan(10) // landed somewhere near index 20, not reset to 0

    // Reactivate: onActivated must restore via the saved {itemIndex, offsetWithinItem} — not
    // reset to the top, which is what a silent regression here would look like.
    await wrapper.setProps({ show: true })
    await new Promise(r => setTimeout(r, 20))
    expect(document.querySelector(`[data-index="${saved.itemIndex}"]`)).toBeTruthy()
    expect(document.querySelector('[data-index="0"]')).toBeFalsy()
  })
})

describe('leading date separator (#1911)', () => {
  it('renders exactly one separator, positioned before the first message, for a single-day session', async () => {
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

    // timestamp close to "now" so formatDateSeparatorLabel() labels it "Today", matching the
    // existing test fixture convention (see CompactionEventGroup.test.js's RECENT_TS).
    const recentTs = Date.now() / 1000
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'First message', timestamp: recentTs }),
      makeMessage({ type: 'user', content: 'Second message', timestamp: recentTs })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    const separators = screen.getAllByRole('separator')
    expect(separators.length).toBe(1)
    expect(separators[0].querySelector('.date-separator-label').textContent).toBe('Today')

    // Positioned before the first message: row 0 is the separator, matching the file's existing
    // data-index="0" idiom for asserting fixed row positions.
    expect(document.querySelector('[data-index="0"]').querySelector('[role="separator"]')).toBeTruthy()
  })

  it('renders a separator above every day, including the first, for a multi-day session', async () => {
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

    // 2 days apart (UTC) so each boundary is unambiguous regardless of local TZ — reusing the
    // pattern from the "does not merge across a date separator" test above.
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({ type: 'assistant', content: 'Day one', timestamp: 1704067200 }),
      makeMessage({ type: 'user', content: 'Day three', timestamp: 1704240000 }),
      makeMessage({ type: 'assistant', content: 'Day five', timestamp: 1704412800 })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await new Promise(r => setTimeout(r, 50))

    expect(screen.getAllByRole('separator').length).toBe(3)
  })

  it('renders no separator for a zero-message session', async () => {
    renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: {
        MessageItem: true,
        TruncationBanner: true,
        SubagentTimeline: true
      }
    })

    await new Promise(r => setTimeout(r, 50))

    expect(screen.queryAllByRole('separator').length).toBe(0)
  })
})

describe('tail-first history (issue #2030, stage 1b)', () => {
  const SESSION = SESSION_ID
  const messagesAt = (from, to) => Array.from({ length: to - from }, (_, i) =>
    makeMessage({ id: `msg-${from + i}`, message_id: `msg-${from + i}`, type: 'user', content: `Message ${from + i}`, timestamp: 1700000000 + from + i })
  )

  function stubScrollMechanics(scrollEl) {
    Element.prototype.scrollTo = function (opts) {
      const top = typeof opts === 'object' && opts !== null ? opts.top : undefined
      if (typeof top === 'number') {
        this.scrollTop = top
        this.dispatchEvent(new Event('scroll'))
      }
    }
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, get: () => 100000 })
    Object.defineProperty(scrollEl, 'clientHeight', { configurable: true, get: () => 600 })
  }

  async function mountWithStore(stubs = {}) {
    const rendered = renderWithStores(MessageList, {
      provide: { viewSessionId: viewSessionIdRef },
      stubs: { MessageItem: MESSAGE_ITEM_STUB, TruncationBanner: true, SubagentTimeline: true, ...stubs },
    })
    const { useMessageStore } = await import('@/stores/message')
    return { ...rendered, messageStore: useMessageStore(rendered.pinia) }
  }

  async function installTail(messageStore, messages, { hasMoreBefore = true } = {}) {
    const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
    const all = [...messagesAt(0, 30), ...messages]
    const server = createFakeHistoryServer(all, { tailStart: hasMoreBefore ? 30 : 0, pageSize: 30 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
    return all
  }

  it('shows a non-blocking "loading older history" banner while the history is incomplete, and removes it when complete', async () => {
    const { messageStore } = await mountWithStore()
    await installTail(messageStore, messagesAt(30, 60))
    await new Promise(r => setTimeout(r, 50))

    const banner = document.querySelector('[data-testid="history-banner"]')
    expect(banner).toBeTruthy()
    expect(banner.textContent).toContain('Showing newest messages')
    expect(banner.textContent).toContain('loading older history')
    // The banner sits outside the scroll container, so it can never shift scroll coordinates.
    expect(document.querySelector('.messages-area').contains(banner)).toBe(false)

    messageStore.applyFetchedOlderPage(SESSION, await messageStore.fetchOlderPage(SESSION, { signal: new AbortController().signal }))
    await new Promise(r => setTimeout(r, 50))
    expect(document.querySelector('[data-testid="history-banner"]')).toBeFalsy()
  })

  it('renders no banner for a session shorter than one window', async () => {
    const { messageStore } = await mountWithStore()
    await installTail(messageStore, messagesAt(30, 40), { hasMoreBefore: false })
    await new Promise(r => setTimeout(r, 50))
    expect(document.querySelector('[data-testid="history-banner"]')).toBeFalsy()
  })

  it('slow-link mode shows the manual banner with "Load older history" and "Load full history"', async () => {
    const { historyBackfill } = await import('@/composables/useHistoryBackfill')
    const { messageStore } = await mountWithStore()
    await installTail(messageStore, messagesAt(30, 60))
    historyBackfill.statusBySession.value = new Map([[SESSION, { mode: 'manual', paused: null, loading: false, error: null }]])
    await new Promise(r => setTimeout(r, 50))

    expect(document.querySelector('[data-testid="history-banner"]').textContent).toContain("isn't loaded automatically")
    const loadOlder = vi.spyOn(historyBackfill, 'requestOlder').mockImplementation(() => {})
    const loadAll = vi.spyOn(historyBackfill, 'loadAll').mockImplementation(() => {})
    await fireEvent.click(document.querySelector('[data-testid="history-load-older"]'))
    expect(loadOlder).toHaveBeenCalledWith(SESSION)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await fireEvent.click(document.querySelector('[data-testid="history-load-full"]'))
    expect(loadAll).toHaveBeenCalledWith(SESSION)
    historyBackfill.statusBySession.value = new Map()
    vi.restoreAllMocks()
  })

  describe('turn navigation over the history index (stage 2 PR B)', () => {
    const turnIds = (all) => all.filter(m => m.type === 'user').map(m => m.message_id)

    async function mountTail({ index } = {}) {
      const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
      const mounted = await mountWithStore()
      const all = [...messagesAt(0, 30), ...messagesAt(30, 60)]
      const server = createFakeHistoryServer(all, { tailStart: 30, pageSize: 30, index: index === undefined ? { turns: turnIds(all) } : index })
      apiMock.get.mockImplementation((url) => server.handle(url))
      await mounted.messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
      await new Promise(r => setTimeout(r, 50))
      stubScrollMechanics(mounted.container.querySelector('.messages-area'))
      return { ...mounted, all }
    }

    it('no usable index yet: pending (idle -> loading -> unavailable keeps Prev locked); Next unaffected', async () => {
      const { wrapper, messageStore } = await mountTail({ index: null })
      expect(wrapper.vm.findPrevTurn()).toEqual({ kind: 'pending', reason: 'idle' })
      const pending = messageStore.ensureHistoryIndex(SESSION)
      expect(wrapper.vm.findPrevTurn()).toEqual({ kind: 'pending', reason: 'loading' })
      await pending // old server: 404
      expect(wrapper.vm.findPrevTurn()).toEqual({ kind: 'pending', reason: 'unavailable' })
    })

    it('with the index ready, the previous turn above the loaded suffix is the newest older index turn', async () => {
      const { wrapper, messageStore } = await mountTail()
      await messageStore.ensureHistoryIndex(SESSION)
      // viewport at the top of the loaded list: the first loaded turn row is visible, none above it
      expect(wrapper.vm.findPrevTurn()).toEqual({ kind: 'unloaded', messageId: 'msg-29', ordinal: 30, sizeHint: 29, cursor: 'b:29' })
    })

    it('a loaded turn above the viewport wins over the index', async () => {
      const { wrapper, messageStore } = await mountTail()
      await messageStore.ensureHistoryIndex(SESSION)
      await wrapper.vm.scrollToItemIndex(25, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 30))
      expect(wrapper.vm.findPrevTurn()).toMatchObject({ kind: 'loaded' })
    })

    it('there is no previous turn when the oldest turn of a complete history is in view', async () => {
      const { wrapper, messageStore, container } = await mountWithStore()
      messageStore.messagesBySession.set(SESSION, messagesAt(0, 1))
      messageStore.messagesBySession = new Map(messageStore.messagesBySession)
      await new Promise(r => setTimeout(r, 50))
      stubScrollMechanics(container.querySelector('.messages-area'))
      expect(messageStore.isHistoryComplete(SESSION)).toBe(true)
      expect(wrapper.vm.findPrevTurn()).toBeNull()
    })

    it('there is no previous turn when the index has nothing older than the loaded suffix', async () => {
      const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
      const { wrapper, messageStore, container } = await mountWithStore()
      const all = messagesAt(0, 40)
      // the server snapped the window at the very first turn, but older records (non-turn) remain
      const server = createFakeHistoryServer(all, { tailStart: 0, pageSize: 30, index: { turns: all.map(m => m.message_id) } })
      apiMock.get.mockImplementation((url) => server.handle(url))
      await messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
      await new Promise(r => setTimeout(r, 50))
      stubScrollMechanics(container.querySelector('.messages-area'))
      await wrapper.vm.scrollToItemIndex(0, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 30))
      expect(messageStore.isHistoryComplete(SESSION)).toBe(true)
      expect(wrapper.vm.findPrevTurn()).toBeNull()
    })

    it('scrollToTurnMessage scrolls to a loaded turn and reports false for one that is not loaded', async () => {
      const { wrapper } = await mountTail()
      await expect(wrapper.vm.scrollToTurnMessage('msg-45')).resolves.toBeTruthy()
      await expect(wrapper.vm.scrollToTurnMessage('msg-3')).resolves.toBe(false)
    })

    it('beginTurnJump drops the sticky-to-bottom pin (so the prepended page cannot snap the list back); endTurnJump re-derives it', async () => {
      const { wrapper, pinia, container } = await mountTail()
      const { useUIStore } = await import('@/stores/ui')
      const setSticky = vi.spyOn(useUIStore(pinia), 'setStickyToBottom')
      wrapper.vm.beginTurnJump()
      expect(setSticky).toHaveBeenLastCalledWith(expect.anything(), false)
      // list is at the very bottom (jsdom: scrollTop 0 of scrollHeight 100000 is NOT bottom) -> not sticky
      wrapper.vm.endTurnJump()
      expect(setSticky).toHaveBeenLastCalledWith(expect.anything(), false)
      const el = container.querySelector('.messages-area')
      Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 600 })
      wrapper.vm.endTurnJump() // content fits: back at the bottom -> sticky again
      expect(setSticky).toHaveBeenLastCalledWith(expect.anything(), true)
    })

    it('shows the jump banner and a Cancel button that cancels the jump', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { messageStore } = await mountTail()
      const cancel = vi.spyOn(historyBackfill, 'cancelJump').mockImplementation(() => {})
      historyBackfill.statusBySession.value = new Map([[SESSION, {
        mode: 'auto', paused: null, loading: true, error: null,
        jump: { ordinal: 4 },
      }]])
      await new Promise(r => setTimeout(r, 50))
      const banner = document.querySelector('[data-testid="history-banner"]')
      expect(banner.textContent).toContain('Loading turn 4…')
      await fireEvent.click(document.querySelector('[data-testid="history-jump-cancel"]'))
      expect(cancel).toHaveBeenCalledWith(SESSION)
      historyBackfill.statusBySession.value = new Map()
      vi.restoreAllMocks()
    })
  })

  describe('search hit navigation (issue #2030, stage 3 PR 3E)', () => {
    async function mountTail() {
      const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
      const mounted = await mountWithStore()
      const all = [...messagesAt(0, 30), ...messagesAt(30, 60)]
      const server = createFakeHistoryServer(all, { tailStart: 30, pageSize: 30 })
      apiMock.get.mockImplementation((url) => server.handle(url))
      await mounted.messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
      await new Promise(r => setTimeout(r, 50))
      stubScrollMechanics(mounted.container.querySelector('.messages-area'))
      return mounted
    }

    const hit = (id, sizeHint) => ({ messageId: id, toolId: null, field: 'content', matchStart: 0, matchLength: 2, occurrenceIndex: 0, cursor: `b:${sizeHint}`, sizeHint })

    it('a hit whose row is loaded just scrolls: no jump, no confirm', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper } = await mountTail()
      const jumpAround = vi.spyOn(historyBackfill, 'jumpAround')
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
      await wrapper.vm.scrollToMessageMatch(hit('msg-45', 45))
      expect(jumpAround).not.toHaveBeenCalled()
      expect(confirm).not.toHaveBeenCalled()
      vi.restoreAllMocks()
    })

    it('a hit before the loaded suffix opens ONE around window by the hit cursor (an island), then scrolls to it; no confirm on a fast link', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper, messageStore, pinia } = await mountTail()
      const { useUIStore } = await import('@/stores/ui')
      const setSticky = vi.spyOn(useUIStore(pinia), 'setStickyToBottom')
      const suffixBefore = messageStore.messagesBySession.get(SESSION).length
      const jumpFn = vi.spyOn(historyBackfill, 'jumpAround')
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

      const result = await wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      expect(jumpFn).toHaveBeenCalledWith(SESSION, { cursor: 'b:5' }, { label: 'search result' })
      expect(confirm).not.toHaveBeenCalled()
      expect(messageStore.isMessageLoaded(SESSION, 'msg-5')).toBe(true)
      expect(messageStore.getIsland(SESSION)).toBeTruthy()
      expect(messageStore.messagesBySession.get(SESSION)).toHaveLength(suffixBefore) // the suffix is untouched
      expect(result).toBeTruthy()
      expect(setSticky).toHaveBeenCalledWith(expect.anything(), false) // beginTurnJump
      vi.restoreAllMocks()
    })

    it('a far hit on a fast link never asks, whatever the distance', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper, messageStore } = await mountTail()
      const start = Number(/^b:(\d+)$/.exec(messageStore.getHistoryState(SESSION).startCursor)[1])
      const jumpAround = vi.spyOn(historyBackfill, 'jumpAround')
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
      messageStore.getHistoryState(SESSION).startCursor = `b:${start + 50 * 1024 * 1024}`
      await wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      expect(confirm).not.toHaveBeenCalled()
      expect(jumpAround).toHaveBeenCalledTimes(1)
      vi.restoreAllMocks()
    })

    it('slow link always asks first, naming the window size; declining starts no jump, OK does', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper } = await mountTail()
      vi.spyOn(historyBackfill, 'getMode').mockReturnValue('manual')
      const jumpAround = vi.spyOn(historyBackfill, 'jumpAround')
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
      await expect(wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))).resolves.toBe(false)
      expect(confirm).toHaveBeenCalledTimes(1)
      expect(confirm.mock.calls[0][0]).toBe('Jump to search result? Loads about 0.5 MB of history')
      expect(jumpAround).not.toHaveBeenCalled()
      confirm.mockReturnValue(true)
      await wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      expect(jumpAround).toHaveBeenCalledTimes(1)
      vi.restoreAllMocks()
    })

    it('a jump that ends cancelled leaves the list alone; not-found marks the match as not locatable', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper, messageStore } = await mountTail()
      await fireEvent.click(document.querySelector('.message-search-toggle'))
      await fireEvent.update(document.querySelector('.message-search-input'), 'x')
      vi.spyOn(historyBackfill, 'jumpAround').mockImplementation(() => {
        const err = Object.assign(new Error('Jump cancelled'), { reason: 'aborted' })
        return { promise: Promise.reject(err), cancel: vi.fn() }
      })
      await wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      expect(document.querySelector('.message-search-count').textContent).not.toContain("Couldn't locate")

      vi.spyOn(historyBackfill, 'jumpAround').mockImplementation(() => {
        const err = Object.assign(new Error('Message not found'), { reason: 'not-found' })
        return { promise: Promise.reject(err), cancel: vi.fn() }
      })
      await wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      await new Promise(r => setTimeout(r, 20))
      expect(document.querySelector('.message-search-count').textContent).toContain("Couldn't locate")
      expect(messageStore.getHistoryState(SESSION).hasMoreBefore).toBe(true)
      expect(messageStore.getIsland(SESSION)).toBeNull()
      vi.restoreAllMocks()
    })

    it('a newer navigation cancels the pending search jump', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper } = await mountTail()
      const cancel = vi.fn()
      vi.spyOn(historyBackfill, 'jumpAround').mockImplementation(() => ({ promise: new Promise(() => {}), cancel }))
      wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      await new Promise(r => setTimeout(r, 10))
      await wrapper.vm.scrollToMessageMatch(hit('msg-45', 45))
      expect(cancel).toHaveBeenCalledTimes(1)
      vi.restoreAllMocks()
    })

    it('a declined confirm leaves the sticky pin alone (no jump began, so none ends)', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper, pinia } = await mountTail()
      const { useUIStore } = await import('@/stores/ui')
      const setSticky = vi.spyOn(useUIStore(pinia), 'setStickyToBottom')
      vi.spyOn(historyBackfill, 'getMode').mockReturnValue('manual')
      vi.spyOn(window, 'confirm').mockReturnValue(false)
      await wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      expect(setSticky).not.toHaveBeenCalled()
      vi.restoreAllMocks()
    })

    it('overlapping jumps re-derive the sticky state only when the last one ends', async () => {
      const { wrapper, container, pinia } = await mountTail()
      const { useUIStore } = await import('@/stores/ui')
      const setSticky = vi.spyOn(useUIStore(pinia), 'setStickyToBottom')
      const el = container.querySelector('.messages-area')
      Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 600 }) // content fits: "at bottom"
      wrapper.vm.beginTurnJump()
      wrapper.vm.beginTurnJump()
      setSticky.mockClear()
      wrapper.vm.endTurnJump() // the superseded jump ends first: no recompute while the newer one runs
      expect(setSticky).not.toHaveBeenCalled()
      wrapper.vm.endTurnJump()
      expect(setSticky).toHaveBeenLastCalledWith(expect.anything(), true)
    })

    it('closing the search cancels a pending search jump', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { wrapper } = await mountTail()
      const cancel = vi.fn()
      vi.spyOn(historyBackfill, 'jumpAround').mockImplementation(() => ({ promise: new Promise(() => {}), cancel }))
      await fireEvent.click(document.querySelector('.message-search-toggle'))
      wrapper.vm.scrollToMessageMatch(hit('msg-5', 5))
      await new Promise(r => setTimeout(r, 10))
      await fireEvent.click(document.querySelector('[aria-label="Close search"]'))
      await new Promise(r => setTimeout(r, 10))
      expect(cancel).toHaveBeenCalledTimes(1)
      vi.restoreAllMocks()
    })

    describe('highlight of the current server hit', () => {
      const SEARCH_SESSION_ROWS = [
        { id: 'msg-45', message_id: 'msg-45', type: 'user', content: 'alpha one alpha two', timestamp: 1700000045 },
        { id: 'msg-50', message_id: 'msg-50', type: 'user', content: 'plain alpha here', timestamp: 1700000050 },
      ]
      const row = (id, extra = {}) => ({
        message_id: id, tool_id: null, field: 'content', match_start: 0, match_length: 5, snippet: 's',
        occurrence_index: 0, cursor: 'b:1', size_hint: 1, ...extra,
      })

      // Partial history (tail loaded); /search answers the scripted newest-first page.
      async function mountSearchable() {
        const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
        const mounted = await mountWithStore()
        const all = [...messagesAt(0, 30), ...messagesAt(30, 45), ...SEARCH_SESSION_ROWS, ...messagesAt(51, 60)]
        const server = createFakeHistoryServer(all, { tailStart: 30, pageSize: 30 })
        apiMock.get.mockImplementation((url) => {
          if (url.includes('/search?')) {
            return Promise.resolve({
              results: [row('msg-50'), row('msg-45', { occurrence_index: 0, match_start: 0 }), row('msg-45', { occurrence_index: 1, match_start: 10 })],
              next_cursor: null, exhausted: true, history_id: 'h',
            })
          }
          return server.handle(url)
        })
        await mounted.messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
        await new Promise(r => setTimeout(r, 50))
        stubScrollMechanics(mounted.container.querySelector('.messages-area'))
        Element.prototype.scrollIntoView = vi.fn()
        return mounted
      }

      const marks = () => [...document.querySelectorAll('mark.message-search-highlight')]
      const markedOccurrence = (mark) => {
        // the index of this <mark> among all 'alpha' occurrences of its row
        const text = mark.closest('[role="article"]').textContent
        const before = [...mark.closest('[role="article"]').childNodes]
        let seen = 0
        for (const node of before) {
          if (node === mark) return seen
          seen += (node.textContent.match(/alpha/gi) || []).length
        }
        return text.length
      }
      const waitFor = async (fn) => { for (let i = 0; i < 40 && !fn(); i++) await new Promise(r => setTimeout(r, 25)) }

      it('applies the highlight to the right occurrence of a loaded server hit, and next/prev moves it', async () => {
        await mountSearchable()
        await fireEvent.click(document.querySelector('.message-search-toggle'))
        await fireEvent.update(document.querySelector('.message-search-input'), 'alpha')
        await new Promise(r => setTimeout(r, 400)) // debounce + page
        await waitFor(() => marks().length === 1)
        expect(document.querySelector('.message-search-count').textContent).toBe('1 of 3')
        // result 1 = newest hit = msg-50, its first (only) occurrence
        expect(marks()).toHaveLength(1)
        expect(marks()[0].closest('[role="article"]').textContent).toContain('plain')
        expect(marks()[0].textContent).toBe('alpha')

        // next: msg-45 occurrence 0, next: msg-45 occurrence 1 (the SECOND "alpha" of the row)
        await fireEvent.click(document.querySelector('[aria-label="Next match"]'))
        await waitFor(() => marks().length === 1 && marks()[0].closest('[role="article"]').textContent.includes('one'))
        expect(marks()[0].closest('[role="article"]').textContent).toContain('alpha one alpha two')
        expect(markedOccurrence(marks()[0])).toBe(0)
        await fireEvent.click(document.querySelector('[aria-label="Next match"]'))
        await waitFor(() => marks().length === 1 && markedOccurrence(marks()[0]) === 1)
        expect(markedOccurrence(marks()[0])).toBe(1)
        expect(document.querySelector('.message-search-count').textContent).toBe('3 of 3')

        // prev moves it back
        await fireEvent.click(document.querySelector('[aria-label="Previous match"]'))
        await waitFor(() => marks().length === 1 && markedOccurrence(marks()[0]) === 0)
        expect(markedOccurrence(marks()[0])).toBe(0)
        expect(document.querySelector('.message-search-count').textContent).toBe('2 of 3')
      })

      // Counts the MutationObservers that are observing right now (the keeper's is the only one MessageList creates).
      function trackObservers() {
        const live = new Set()
        const observe = vi.spyOn(MutationObserver.prototype, 'observe').mockImplementation(function () { live.add(this) })
        const disconnect = vi.spyOn(MutationObserver.prototype, 'disconnect').mockImplementation(function () { live.delete(this) })
        return { live, restore: () => { observe.mockRestore(); disconnect.mockRestore() } }
      }

      async function searchAlpha() {
        await fireEvent.click(document.querySelector('.message-search-toggle'))
        await fireEvent.update(document.querySelector('.message-search-input'), 'alpha')
        await new Promise(r => setTimeout(r, 400))
        await waitFor(() => marks().length === 1)
      }

      it('the highlight keeper exists only while a hit is current, and is torn down on close, user scroll, query edit and a new navigation', async () => {
        const tracker = trackObservers()
        const { wrapper, container } = await mountSearchable()
        expect(tracker.live.size).toBe(0) // search closed: nothing observes
        await searchAlpha()
        expect(tracker.live.size).toBe(1)

        // new navigation (next): the old keeper is replaced, never stacked
        await fireEvent.click(document.querySelector('[aria-label="Next match"]'))
        await waitFor(() => marks().length === 1 && marks()[0].closest('[role="article"]').textContent.includes('one'))
        await new Promise(r => setTimeout(r, 300))
        expect(tracker.live.size).toBe(1)

        // user scroll by hand: the keeper ends, a dropped mark is no longer re-applied
        container.querySelector('.messages-area').dispatchEvent(new WheelEvent('wheel'))
        expect(tracker.live.size).toBe(0)
        const article = marks()[0].closest('[role="article"]')
        article.textContent = article.textContent
        await new Promise(r => setTimeout(r, 400))
        expect(marks()).toHaveLength(0)

        // a fresh navigation starts one again; editing the query ends it
        await fireEvent.click(document.querySelector('[aria-label="Previous match"]'))
        await waitFor(() => tracker.live.size === 1)
        await fireEvent.update(document.querySelector('.message-search-input'), 'alpha x')
        expect(tracker.live.size).toBe(0)

        // closing the search ends it too
        await fireEvent.update(document.querySelector('.message-search-input'), 'alpha')
        await new Promise(r => setTimeout(r, 450))
        await waitFor(() => tracker.live.size === 1)
        await fireEvent.click(document.querySelector('[aria-label="Close search"]'))
        await new Promise(r => setTimeout(r, 30))
        expect(tracker.live.size).toBe(0)
        wrapper.unmount()
        tracker.restore()
      })

      it('editing the query drops the old mark, and a user scroll during a far jump means no keeper starts after it lands', async () => {
        const tracker = trackObservers()
        const { wrapper, container, messageStore } = await mountSearchable()
        await searchAlpha()
        await fireEvent.update(document.querySelector('.message-search-input'), 'alpha x')
        expect(marks()).toHaveLength(0)
        expect(tracker.live.size).toBe(0)

        const { historyBackfill } = await import('@/composables/useHistoryBackfill')
        vi.spyOn(historyBackfill, 'jumpAround').mockImplementation((sessionId, target) => {
          const promise = (async () => {
            container.querySelector('.messages-area').dispatchEvent(new WheelEvent('wheel')) // the user scrolls away mid-jump
            return messageStore.loadAround(sessionId, target)
          })()
          return { promise, cancel: vi.fn() }
        })
        await fireEvent.update(document.querySelector('.message-search-input'), 'Message')
        await wrapper.vm.scrollToMessageMatch({ messageId: 'msg-5', toolId: null, field: 'content', matchStart: 0, matchLength: 7, occurrenceIndex: 0, cursor: 'b:5', sizeHint: 5 })
        await new Promise(r => setTimeout(r, 60)) // inside the 200 ms search debounce: no result navigation yet
        expect(marks().length).toBeGreaterThan(0) // the highlight itself was applied
        expect(tracker.live.size).toBe(0)
        vi.restoreAllMocks()
        tracker.restore()
      })

      it('unmounting the list ends the keeper', async () => {
        const tracker = trackObservers()
        const { wrapper } = await mountSearchable()
        await searchAlpha()
        expect(tracker.live.size).toBe(1)
        wrapper.unmount()
        expect(tracker.live.size).toBe(0)
        tracker.restore()
      })

      it('re-applies the highlight when the row re-renders and drops it', async () => {
        await mountSearchable()
        await fireEvent.click(document.querySelector('.message-search-toggle'))
        await fireEvent.update(document.querySelector('.message-search-input'), 'alpha')
        await new Promise(r => setTimeout(r, 400))
        await waitFor(() => marks().length === 1)
        // simulate a re-render of the row: the mark disappears from the DOM
        const article = marks()[0].closest('[role="article"]')
        article.textContent = article.textContent
        expect(marks()).toHaveLength(0)
        await waitFor(() => marks().length === 1)
        expect(marks()).toHaveLength(1)
        expect(marks()[0].textContent).toBe('alpha')
      })
    })

    it('the jump banner names the search result', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      await mountTail()
      historyBackfill.statusBySession.value = new Map([[SESSION, {
        mode: 'auto', paused: null, loading: true, error: null,
        jump: { label: 'search result' },
      }]])
      await new Promise(r => setTimeout(r, 50))
      expect(document.querySelector('[data-testid="history-banner"]').textContent).toContain('Loading search result…')
      historyBackfill.statusBySession.value = new Map()
    })
  })

  describe('island: a jumped-to window next to the live suffix (stage 3 PR 3C-1)', () => {
    async function mountIsland(serverOptions = {}) {
      const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
      const mounted = await mountWithStore()
      const all = [...messagesAt(0, 30), ...messagesAt(30, 60)]
      const server = createFakeHistoryServer(all, { tailStart: 45, pageSize: 30, aroundBefore: 6, aroundAfter: 6, afterPageSize: 4, ...serverOptions })
      apiMock.get.mockImplementation((url) => server.handle(url))
      await mounted.messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
      await new Promise(r => setTimeout(r, 50))
      stubScrollMechanics(mounted.container.querySelector('.messages-area'))
      return { ...mounted, server, all }
    }
    // The list opens at the live end: bring the island (rows 0..) and its gap row into the mounted range.
    async function showIslandTop(wrapper) {
      await wrapper.vm.scrollToItemIndex(0, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 80))
    }
    // ...and bring the gap row (just below the island's last row) into the mounted range.
    async function showGap(wrapper) {
      for (let index = 14; index < 40 && !document.querySelector('[data-testid="history-gap-row"]'); index += 2) {
        await wrapper.vm.scrollToItemIndex(index, { align: 'start', behavior: 'auto' })
        await new Promise(r => setTimeout(r, 40))
      }
    }

    it('renders the island, then ONE gap row (size text, Load newer, Jump to latest), then the suffix', async () => {
      const { messageStore, wrapper } = await mountIsland()
      await messageStore.loadAround(SESSION, { cursor: 'b:10' })
      await showGap(wrapper)
      const gap = document.querySelector('[data-testid="history-gap-row"]')
      expect(gap).toBeTruthy()
      expect(document.querySelectorAll('[data-testid="history-gap-row"]')).toHaveLength(1)
      expect(gap.textContent).toMatch(/of history not loaded/)
      expect(document.querySelector('[data-testid="history-gap-load-newer"]')).toBeTruthy()
      expect(document.querySelector('[data-testid="history-gap-jump-latest"]')).toBeTruthy()
    })

    it('with no island there is no gap row and the rows are the plain pipeline output', async () => {
      await mountIsland()
      expect(document.querySelector('[data-testid="history-gap-row"]')).toBeNull()
    })

    it('Load newer fetches one after-page bounded by the suffix start; reaching the suffix merges and removes the gap', async () => {
      const { messageStore, server, wrapper } = await mountIsland()
      await messageStore.loadAround(SESSION, { cursor: 'b:10' })
      let guard = 0
      while (messageStore.getIsland(SESSION) && guard++ < 20) {
        await showGap(wrapper)
        const button = document.querySelector('[data-testid="history-gap-load-newer"]')
        if (button) await fireEvent.click(button)
        await new Promise(r => setTimeout(r, 60))
      }
      expect(server.requests.filter(u => u.includes('after=')).length).toBeGreaterThan(1)
      expect(server.requests.filter(u => u.includes('after=')).every(u => u.includes('until='))).toBe(true)
      expect(messageStore.getIsland(SESSION)).toBeNull()
      await new Promise(r => setTimeout(r, 50))
      expect(document.querySelector('[data-testid="history-gap-row"]')).toBeNull()
    })

    it('Jump to latest scrolls to the live end and keeps the island loaded', async () => {
      const { messageStore, wrapper } = await mountIsland()
      await messageStore.loadAround(SESSION, { cursor: 'b:10' })
      await showGap(wrapper)
      const scroll = vi.spyOn(wrapper.vm.$el.querySelector('.messages-area'), 'scrollTop', 'set')
      await fireEvent.click(document.querySelector('[data-testid="history-gap-jump-latest"]'))
      await new Promise(r => setTimeout(r, 60))
      expect(messageStore.getIsland(SESSION)).toBeTruthy()
      expect(scroll).toHaveBeenCalled()
    })

    it('row keys stay unique across the interval join (the gap row has its own stable key)', async () => {
      const { messageStore } = await mountIsland()
      await messageStore.loadAround(SESSION, { cursor: 'b:10' })
      await new Promise(r => setTimeout(r, 80))
      const { computeItemKeys, buildIntervalItems } = await import('@/utils/displayableItems')
      const island = messageStore.getIsland(SESSION)
      const items = buildIntervalItems({
        sessionId: SESSION, legEntries: [], getTaskIdForLaunchToolUse: () => null, isHookMessageAttached: () => false,
        intervals: [
          { messages: island.messages, toolCalls: island.toolCalls, hasMoreBefore: true, hasMoreAfter: true },
          { messages: messageStore.messagesBySession.get(SESSION), toolCalls: [], hasMoreBefore: true, hasMoreAfter: false },
        ],
        gap: { fromCursor: island.endCursor, toCursor: 'b:45', approxBytes: 1000 },
      })
      const keys = computeItemKeys(items)
      expect(new Set(keys).size).toBe(keys.length)
      expect(keys).toContain(`gap:${island.endCursor}`)
    })

    it('scrolling to the top of the island pages the island upward (not the suffix)', async () => {
      const { historyBackfill } = await import('@/composables/useHistoryBackfill')
      const { messageStore, wrapper } = await mountIsland({ aroundBefore: 3 })
      const olderIsland = vi.spyOn(historyBackfill, 'requestIslandOlder').mockResolvedValue(false)
      const olderSuffix = vi.spyOn(historyBackfill, 'requestOlder')
      await messageStore.loadAround(SESSION, { cursor: 'b:20' }) // the window starts below the file start: older history remains
      await new Promise(r => setTimeout(r, 80))
      olderIsland.mockClear()
      await wrapper.vm.scrollToItemIndex(30, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 80))
      expect(olderIsland).not.toHaveBeenCalled() // far from the island's top
      await wrapper.vm.scrollToItemIndex(0, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 80))
      expect(olderIsland).toHaveBeenCalledWith(SESSION)
      expect(olderSuffix).not.toHaveBeenCalled()
      vi.restoreAllMocks()
    })

    it('with the history index: Prev at the suffix top is the turn right before it (unloaded), Next at the island bottom the first turn after it; inside an interval both are plain loaded rows', async () => {
      const { messageStore, wrapper, all } = await mountIsland({ aroundBefore: 3 })
      const turnIds = all.filter(m => m.type === 'user').map(m => m.message_id)
      const real = apiMock.get.getMockImplementation()
      apiMock.get.mockImplementation((url) => (String(url).includes('/history_index')
        ? Promise.resolve({ history_id: 'h1', total_bytes: all.length, turns: turnIds.map(id => { const i = all.findIndex(m => m.message_id === id); return [`b:${i}`, id, i] }), compactions: [], event_cursor: 1, epoch: 'e' })
        : real(url)))
      await messageStore.ensureHistoryIndex(SESSION)
      await messageStore.loadAround(SESSION, { cursor: 'b:10' })
      await new Promise(r => setTimeout(r, 80))
      const island = messageStore.getIsland(SESSION)
      expect(island.endCursor).toBe('b:16')
      await showGap(wrapper)
      const rows = [...document.querySelectorAll('.virtual-item-row')]
      const gapIndex = Number(rows.find(r => r.querySelector('[data-testid="history-gap-row"]')).dataset.index)
      // inside the island: the previous/next turns are loaded rows
      await wrapper.vm.scrollToItemIndex(gapIndex - 3, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 60))
      expect(wrapper.vm.findPrevTurn()).toMatchObject({ kind: 'loaded' })
      // viewport at the island's last rows: Next is the first turn after the island end (record 16), not loaded
      await wrapper.vm.scrollToItemIndex(gapIndex - 1, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 60))
      expect(wrapper.vm.findNextTurn()).toMatchObject({ kind: 'unloaded', messageId: 'msg-16', cursor: 'b:16' })
      // viewport at the top of the suffix (the first suffix turn): Prev is the turn right before the suffix start (record 44)
      await wrapper.vm.scrollToItemIndex(gapIndex + 2, { align: 'start', behavior: 'auto' })
      await new Promise(r => setTimeout(r, 60))
      expect(wrapper.vm.findPrevTurn()).toMatchObject({ kind: 'unloaded', messageId: 'msg-44', cursor: 'b:44' })
    })
  })

  it('keeps the same row at the same viewport offset when an older page is prepended (anchor-preserving prepend)', async () => {
    const { wrapper, container, messageStore } = await mountWithStore()
    await installTail(messageStore, messagesAt(30, 90))
    await new Promise(r => setTimeout(r, 50))
    stubScrollMechanics(container.querySelector('.messages-area'))

    // Scroll well into the loaded list so the anchor row is mid-viewport, not the first row.
    await wrapper.vm.scrollToItemIndex(25, { align: 'start', behavior: 'auto' })
    await new Promise(r => setTimeout(r, 30))

    const scrollEl = container.querySelector('.messages-area')
    const rowOffsetFromViewport = (text) => {
      const row = [...document.querySelectorAll('.virtual-item-row')].find(r => r.textContent.includes(text))
      if (!row) return null
      return Number(/translateY\(([\d.]+)px\)/.exec(row.style.transform)[1]) - scrollEl.scrollTop
    }
    const before = rowOffsetFromViewport('Message 55')
    expect(before).not.toBeNull()

    messageStore.applyFetchedOlderPage(SESSION, await messageStore.fetchOlderPage(SESSION, { signal: new AbortController().signal }))
    await new Promise(r => setTimeout(r, 50))

    const after = rowOffsetFromViewport('Message 55')
    expect(after).not.toBeNull()
    expect(Math.abs(after - before)).toBeLessThanOrEqual(2)
  })

  it('does not stack leg-terminal signals of pre-window legs at the top while history is incomplete', async () => {
    const { messageStore } = await mountWithStore({ SubagentAnchorRow: { template: '<div data-testid="signal-row" />', props: ['item'] } })
    await installTail(messageStore, messagesAt(30, 40))
    for (const taskId of ['old-1', 'old-2', 'old-3']) {
      messageStore.applyTaskLifecycleFrame(SESSION, 'task_started', { task_id: taskId, tool_use_id: `tu-${taskId}`, description: taskId }, 1700000001)
      messageStore.applyTaskLifecycleFrame(SESSION, 'task_notification', { task_id: taskId, status: 'completed', summary: 'done' }, 1700000002)
    }
    await new Promise(r => setTimeout(r, 50))
    expect(document.querySelectorAll('[data-testid="signal-row"]')).toHaveLength(0)
  })

  it('a jump in flight does not re-derive the sticky pin from a transient bottom position; endTurnJump re-derives once (stage 3 PR 3C-2)', async () => {
    const { wrapper, messageStore, pinia, container } = await mountWithStore()
    await installTail(messageStore, messagesAt(30, 60))
    await new Promise(r => setTimeout(r, 50))
    const el = container.querySelector('.messages-area')
    stubScrollMechanics(el) // scrollHeight 100000, clientHeight 600
    const { useUIStore } = await import('@/stores/ui')
    const setSticky = vi.spyOn(useUIStore(pinia), 'setStickyToBottom')

    wrapper.vm.beginTurnJump() // sticky off; opens a gesture window so the scroll event is trusted
    setSticky.mockClear()
    // The jump target near the live end is clamped to the very bottom while the rows below are unmeasured.
    el.scrollTop = 100000 - 600
    el.dispatchEvent(new Event('scroll'))
    expect(setSticky).not.toHaveBeenCalledWith(expect.anything(), true)

    wrapper.vm.endTurnJump() // the one re-derivation, from the real position
    expect(setSticky).toHaveBeenCalledWith(expect.anything(), true)
    vi.restoreAllMocks()
  })

  it('a running leg whose launch is outside the window draws no lane (no error); the lane appears once the launch page is applied', async () => {
    const { messageStore } = await mountWithStore()
    const launch = makeMessage({
      id: 'msg-launch', message_id: 'msg-launch', type: 'assistant', content: 'Launching', timestamp: 1700000001,
      metadata: { has_tool_uses: true, tool_uses: [{ id: 'launch-1', name: 'Task', input: {} }], turn_id: 'turn-launch' },
    })
    const { createFakeHistoryServer } = await import('@/stores/__tests__/helpers/historyServer')
    const all = [launch, ...messagesAt(30, 40)]
    const server = createFakeHistoryServer(all, { tailStart: 1, pageSize: 5 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    messageStore.applyTaskLifecycleFrame(SESSION, 'task_started', { task_id: 'task-1', tool_use_id: 'launch-1', description: 'Running' }, 1700000001)
    await messageStore.loadMessages(SESSION, null, 0, { window: 'tail', signal: new AbortController().signal })
    await new Promise(r => setTimeout(r, 50))
    expect(document.querySelectorAll('.gutter-lane')).toHaveLength(0)

    messageStore.applyFetchedOlderPage(SESSION, await messageStore.fetchOlderPage(SESSION, { signal: new AbortController().signal }))
    await new Promise(r => setTimeout(r, 50))
    expect(document.querySelectorAll('.gutter-lane')).toHaveLength(1)
  })
})
