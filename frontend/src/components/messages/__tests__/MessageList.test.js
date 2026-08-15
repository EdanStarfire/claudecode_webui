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
      // Forwarded subagent text (CLAUDE_CODE_FORWARD_SUBAGENT_TEXT) — must not leak as a top-level bubble
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
    // The orphaned tool's messageId matches the EARLIER one.
    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'assistant',
        content: 'First turn — requests permission',
        message_id: 'msg-early',
        metadata: { has_tool_uses: false, tool_uses: [] }
      }),
      makeMessage({ type: 'user', content: 'interjection' }),
      makeMessage({
        type: 'assistant',
        content: 'Second, unrelated turn',
        message_id: 'msg-late',
        metadata: { has_tool_uses: false, tool_uses: [] }
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
