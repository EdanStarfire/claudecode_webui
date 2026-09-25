import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeMessage, makeToolCall } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

describe('message store', () => {
  it('addMessage appends to session bucket', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({ content: 'hi' }))

    expect(store.messagesBySession.get('sess-1').length).toBe(1)
    expect(store.messagesBySession.get('sess-1')[0].content).toBe('hi')
  })

  it('addMessage skips a duplicate user message redelivered with the same message_id (#1845)', async () => {
    // Regression test: backend/message_parser.py's UserMessageHandler now propagates
    // the stable message_id assigned at persistence time, so a user message redelivered
    // live via the poll stream (the jsonl-write/queue-push race #1845 describes) carries
    // the same message_id as its already-loaded counterpart and this dedup catches it.
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({
      type: 'user', content: 'Please help me', message_id: 'msg-user-dup'
    }))
    store.addMessage('sess-1', makeMessage({
      type: 'user', content: 'Please help me', message_id: 'msg-user-dup'
    }))

    expect(store.messagesBySession.get('sess-1').length).toBe(1)
  })

  it('loadMessages stores messages from paged API response', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    const messages = [makeMessage({ content: 'msg1' }), makeMessage({ content: 'msg2' })]
    apiMock.get.mockResolvedValue({
      messages,
      total_count: 2,
      has_more: false,
      event_cursor: 10
    })

    const result = await store.loadMessages('sess-1')

    expect(apiMock.get).toHaveBeenCalledWith(expect.stringContaining('/api/sessions/sess-1/messages'))
    expect(store.messagesBySession.get('sess-1').length).toBe(2)
    expect(result.totalCount).toBe(2)
  })

  it('loadMessages single-page response makes exactly one request (#1747 regression guard)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    apiMock.get.mockResolvedValueOnce({
      messages: [makeMessage({ content: 'only' })],
      total_count: 1,
      has_more: false,
      event_cursor: 5
    })

    const result = await store.loadMessages('sess-1')

    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(result.messages.length).toBe(1)
    expect(result.hasMore).toBe(false)
  })

  it('loadMessages pages through multiple has_more:true responses until has_more:false (#1747)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    apiMock.get
      .mockResolvedValueOnce({
        messages: [makeMessage({ content: 'page1-a' }), makeMessage({ content: 'page1-b' })],
        total_count: 5,
        has_more: true,
        event_cursor: 1
      })
      .mockResolvedValueOnce({
        messages: [makeMessage({ content: 'page2-a' }), makeMessage({ content: 'page2-b' })],
        total_count: 5,
        has_more: true,
        event_cursor: 2
      })
      .mockResolvedValueOnce({
        messages: [makeMessage({ content: 'page3-a' })],
        total_count: 5,
        has_more: false,
        event_cursor: 3
      })

    const result = await store.loadMessages('sess-1')

    expect(apiMock.get).toHaveBeenCalledTimes(3)
    expect(result.messages.map(m => m.content)).toEqual([
      'page1-a', 'page1-b', 'page2-a', 'page2-b', 'page3-a'
    ])
    expect(store.messagesBySession.get('sess-1').length).toBe(5)
    // Issue #1747: offset must advance by the requested page size (10000, loadMessages'
    // default), NOT by the response's messages.length — the backend's offset/limit
    // pagination applies to raw stored lines, while the response can contain more
    // entries than raw lines consumed (synthetic tool_call messages are interleaved).
    // Advancing by response length would desync from the backend's cursor and skip messages.
    expect(apiMock.get.mock.calls[0][0]).toContain('offset=0')
    expect(apiMock.get.mock.calls[1][0]).toContain('offset=10000')
    expect(apiMock.get.mock.calls[2][0]).toContain('offset=20000')
  })

  it('handleToolCall creates new entry then updates on second call', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'Bash',
      input: { command: 'ls' },
      status: 'running'
    })

    let calls = store.toolCallsBySession.get('sess-1')
    expect(calls.length).toBe(1)
    expect(calls[0].status).toBe('executing')

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'Bash',
      input: { command: 'ls' },
      status: 'completed',
      result: 'file.txt'
    })

    calls = store.toolCallsBySession.get('sess-1')
    expect(calls.length).toBe(1)
    expect(calls[0].status).toBe('completed')
    expect(calls[0].result.content).toBe('file.txt')
  })

  it('handlePermissionRequest maps request to tool then handlePermissionResponse updates status', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { useSessionStore } = await import('@/stores/session')
    const store = useMessageStore()
    const sessionStore = useSessionStore()
    sessionStore.currentSessionId = 'sess-1'

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'Edit',
      input: { path: '/tmp/f' },
      status: 'awaiting_permission',
      request_id: 'req-1'
    })

    expect(store.toolCallsBySession.get('sess-1')[0].status).toBe('permission_required')

    store.handlePermissionResponse('sess-1', {
      request_id: 'req-1',
      decision: 'allow'
    })

    const tc = store.toolCallsBySession.get('sess-1')[0]
    expect(tc.permissionDecision).toBe('allow')
    expect(tc.status).toBe('executing')
  })

  it('handleToolCall captures messageId on create and on update (#1694/#1958)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'Bash',
      input: { command: 'ls' },
      status: 'running',
      turn_id: 'msg-abc'
    })

    let calls = store.toolCallsBySession.get('sess-1')
    expect(calls[0].messageId).toBe('msg-abc')

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-2',
      name: 'Edit',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-2'
      // no turn_id — legacy payload
    })

    calls = store.toolCallsBySession.get('sess-1')
    expect(calls[1].messageId).toBeNull()

    // Update branch: a later event for use-2 carries turn_id
    store.handleToolCall('sess-1', {
      tool_use_id: 'use-2',
      name: 'Edit',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-2',
      turn_id: 'msg-def'
    })

    calls = store.toolCallsBySession.get('sess-1')
    expect(calls[1].messageId).toBe('msg-def')
  })

  it('handleToolCall persists AskUserQuestion answers through the terminal completed clobber (#1774)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    // 1. Initial awaiting_permission event, no answers yet
    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'AskUserQuestion',
      input: { questions: [{ question: 'Q1', options: [{ label: 'Option A' }] }] },
      status: 'awaiting_permission',
      request_id: 'req-1'
    })

    let tc = store.toolCallsBySession.get('sess-1')[0]
    expect(tc.answers).toBeNull()

    // 2. Permission-response transition event carries updated_input sibling field
    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'AskUserQuestion',
      input: { questions: [{ question: 'Q1', options: [{ label: 'Option A' }] }] },
      updated_input: {
        questions: [{ question: 'Q1', options: [{ label: 'Option A' }] }],
        answers: { Q1: 'Option A' }
      },
      status: 'running'
    })

    tc = store.toolCallsBySession.get('sess-1')[0]
    expect(tc.answers).toEqual({ Q1: 'Option A' })

    // 3. Terminal completed event carries only the original input, no updated_input —
    // this previously clobbered .input and left answers unrecoverable.
    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'AskUserQuestion',
      input: { questions: [{ question: 'Q1', options: [{ label: 'Option A' }] }] },
      status: 'completed',
      result: 'ok'
    })

    tc = store.toolCallsBySession.get('sess-1')[0]
    expect(tc.status).toBe('completed')
    expect(tc.answers).toEqual({ Q1: 'Option A' })
  })

  it('markToolUseOrphaned marks tool as orphaned', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'Bash',
      input: { command: 'ls' },
      status: 'running'
    })

    store.markToolUseOrphaned('sess-1', 'use-1', 'Session was restarted')

    const tc = store.toolCallsBySession.get('sess-1')[0]
    expect(tc._isOrphaned).toBe(true)
    expect(tc.backendStatus).toBe('interrupted')
    expect(store.isToolUseOrphaned('sess-1', 'use-1')).toBe(true)
  })

  it('markToolUseOrphaned resolves effectiveStatus to orphaned, not permission_required (#1959)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { getEffectiveStatusForTool } = await import('@/composables/useToolStatus')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'AskUserQuestion',
      input: { questions: [] },
      status: 'awaiting_permission'
    })

    store.markToolUseOrphaned('sess-1', 'use-1', 'Session was interrupted')

    const tc = store.toolCallsBySession.get('sess-1')[0]
    expect(getEffectiveStatusForTool(tc)).toBe('orphaned')
  })

  it('syncMessages single-page response makes exactly one request (#1747 regression guard)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    // Seed an existing message so lastReceivedTimestamp is set (sync requires a prior baseline)
    store.addMessage('sess-1', makeMessage({ content: 'seed', timestamp: 1700000000 }))
    apiMock.get.mockReset()

    apiMock.get.mockResolvedValueOnce({
      messages: [makeMessage({ content: 'new', timestamp: 1700000100 })],
      total_count: 2,
      has_more: false
    })

    const result = await store.syncMessages('sess-1')

    expect(apiMock.get).toHaveBeenCalledTimes(1)
    expect(result.syncedCount).toBe(1)
    expect(result.hasMore).toBe(false)
  })

  it('syncMessages pages through multiple responses before applying its timestamp filter (#1747)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    // Seed an existing message so lastReceivedTimestamp is set (sync requires a prior baseline)
    store.addMessage('sess-1', makeMessage({ content: 'seed', timestamp: 1700000000 }))
    apiMock.get.mockReset()

    apiMock.get
      .mockResolvedValueOnce({
        messages: [makeMessage({ content: 'page1', timestamp: 1700000100 })],
        total_count: 3,
        has_more: true
      })
      .mockResolvedValueOnce({
        messages: [makeMessage({ content: 'page2', timestamp: 1700000200 })],
        total_count: 3,
        has_more: false
      })

    const result = await store.syncMessages('sess-1')

    expect(apiMock.get).toHaveBeenCalledTimes(2)
    expect(result.syncedCount).toBe(2)
    expect(result.hasMore).toBe(false)
    const contents = store.messagesBySession.get('sess-1').map(m => m.content)
    expect(contents).toEqual(['seed', 'page1', 'page2'])
  })

  it('syncMessages skips a message whose message_id already exists (#1877 regression)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({
      content: 'seed', timestamp: 1700000000, message_id: 'msg-dup'
    }))
    apiMock.get.mockReset()

    apiMock.get.mockResolvedValueOnce({
      messages: [makeMessage({ content: 'seed', timestamp: 1700000100, message_id: 'msg-dup' })],
      total_count: 1,
      has_more: false
    })

    const result = await store.syncMessages('sess-1')

    expect(result.syncedCount).toBe(0)
    expect(store.messagesBySession.get('sess-1').length).toBe(1)
  })

  it('syncMessages still syncs a genuinely new message_id (#1877)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({
      content: 'seed', timestamp: 1700000000, message_id: 'msg-1'
    }))
    apiMock.get.mockReset()

    apiMock.get.mockResolvedValueOnce({
      messages: [makeMessage({ content: 'new', timestamp: 1700000100, message_id: 'msg-2' })],
      total_count: 2,
      has_more: false
    })

    const result = await store.syncMessages('sess-1')

    expect(result.syncedCount).toBe(1)
    const contents = store.messagesBySession.get('sess-1').map(m => m.content)
    expect(contents).toEqual(['seed', 'new'])
  })

  it('syncMessages passes through multiple keyless messages without deduping them against each other (#1877 AC4)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({ content: 'seed', timestamp: 1700000000 }))
    apiMock.get.mockReset()

    apiMock.get.mockResolvedValueOnce({
      messages: [
        makeMessage({ type: 'system', content: 'sys-a', timestamp: 1700000100 }),
        makeMessage({ type: 'system', content: 'sys-b', timestamp: 1700000200 })
      ],
      total_count: 3,
      has_more: false
    })

    const result = await store.syncMessages('sess-1')

    expect(result.syncedCount).toBe(2)
    const contents = store.messagesBySession.get('sess-1').map(m => m.content)
    expect(contents).toEqual(['seed', 'sys-a', 'sys-b'])
  })
})

// Helpers shared by streaming preview tests
function delta(type, sessionId, event) {
  return { uuid: 'env-' + Math.random(), event: { type, ...event } }
}

describe('content_block_start (tool_use) early tool card (Issue #1573)', () => {
  it('creates a single pending card with correct id/name/status/input', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-early-card'

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))

    const toolCalls = store.toolCallsBySession.get(SID)
    expect(toolCalls.length).toBe(1)
    expect(toolCalls[0]).toMatchObject({
      id: 'toolu_1',
      name: 'Bash',
      status: 'pending',
      input: {}
    })
  })

  it('a subsequent full tool_call for the same id updates the card in place (no duplicate)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-early-card-update'

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_2', name: 'Bash' }
    }))

    store.handleToolCall(SID, {
      tool_use_id: 'toolu_2',
      name: 'Bash',
      input: { command: 'ls -la' },
      status: 'running'
    })

    const toolCalls = store.toolCallsBySession.get(SID)
    expect(toolCalls.length).toBe(1)
    expect(toolCalls[0].status).toBe('executing')
    expect(toolCalls[0].input).toEqual({ command: 'ls -la' })
  })

  it('early card is orphaned (not stuck pending) on interrupt (#1959 precedent)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { getEffectiveStatusForTool } = await import('@/composables/useToolStatus')
    const store = useMessageStore()
    const SID = 'sess-early-card-interrupt'

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_3', name: 'Write' }
    }))

    store.addMessage(SID, makeMessage({ type: 'system', content: '', metadata: { subtype: 'interrupt' } }))

    const tc = store.toolCallsBySession.get(SID).find(t => t.id === 'toolu_3')
    expect(getEffectiveStatusForTool(tc)).toBe('orphaned')
  })

  it('early card is orphaned on restart (client_launched)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { getEffectiveStatusForTool } = await import('@/composables/useToolStatus')
    const store = useMessageStore()
    const SID = 'sess-early-card-restart'

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_4', name: 'Read' }
    }))

    store.addMessage(SID, makeMessage({ type: 'system', content: '', metadata: { subtype: 'client_launched' } }))

    const tc = store.toolCallsBySession.get(SID).find(t => t.id === 'toolu_4')
    expect(getEffectiveStatusForTool(tc)).toBe('orphaned')
  })

  it('does not regress an already-terminal card backward on a stray content_block_start', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-early-card-no-regress'

    store.handleToolCall(SID, {
      tool_use_id: 'toolu_5',
      name: 'Bash',
      input: { command: 'echo done' },
      status: 'completed',
      result: 'done'
    })

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_5', name: 'Bash' }
    }))

    const tc = store.toolCallsBySession.get(SID).find(t => t.id === 'toolu_5')
    expect(tc.status).toBe('completed')
  })

  it('a stray content_block_start for an already-completed tool does not re-open it for the orphan sweep (review fix)', async () => {
    // handleToolCall's status-regression guard silently no-ops when a content_block_start
    // arrives late for a tool that already completed — but the activeToolUses registration
    // must not run independently of that guard, or a subsequent interrupt/restart would
    // orphan a card that already finished successfully.
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-early-card-no-reopen'

    store.handleToolCall(SID, {
      tool_use_id: 'toolu_6',
      name: 'Bash',
      input: { command: 'echo done' },
      status: 'completed',
      result: 'done'
    })

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_6', name: 'Bash' }
    }))

    store.addMessage(SID, makeMessage({ type: 'system', content: '', metadata: { subtype: 'interrupt' } }))

    const tc = store.toolCallsBySession.get(SID).find(t => t.id === 'toolu_6')
    expect(tc.status).toBe('completed')
    expect(tc._isOrphaned).not.toBe(true)
  })

  it('multiple tool calls in one turn resolve to correct count, order, and final state', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-early-card-multi'

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_a', name: 'Read' }
    }))
    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 2,
      content_block: { type: 'tool_use', id: 'toolu_b', name: 'Bash' }
    }))

    store.handleToolCall(SID, {
      tool_use_id: 'toolu_a', name: 'Read', input: { file_path: '/tmp/a' }, status: 'completed', result: 'ok'
    })
    store.handleToolCall(SID, {
      tool_use_id: 'toolu_b', name: 'Bash', input: { command: 'ls' }, status: 'completed', result: 'ok'
    })

    const toolCalls = store.toolCallsBySession.get(SID)
    expect(toolCalls.length).toBe(2)
    expect(toolCalls.map(tc => tc.id)).toEqual(['toolu_a', 'toolu_b'])
    expect(toolCalls.every(tc => tc.status === 'completed')).toBe(true)
  })

  it('ignores content_block_start for non-tool_use blocks (e.g. text)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-early-card-text-block'

    store.handleAssistantDelta(SID, delta('content_block_start', SID, {
      index: 0,
      content_block: { type: 'text' }
    }))

    expect(store.toolCallsBySession.get(SID)).toBeUndefined()
  })
})

describe('addMessage single-rule dedup (Issue #1955)', () => {
  it('pushes a new canonical message', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({ type: 'assistant', content: 'hi', message_id: 'am-1' }))

    expect(store.messagesBySession.get('sess-1').length).toBe(1)
  })

  it('skips an exact backend-id duplicate', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({ type: 'assistant', content: 'hi', message_id: 'am-1' }))
    store.addMessage('sess-1', makeMessage({ type: 'assistant', content: 'hi again', message_id: 'am-1' }))

    const msgs = store.messagesBySession.get('sess-1')
    expect(msgs.length).toBe(1)
    expect(msgs[0].content).toBe('hi')
  })

  it('two different canonical messages with different ids both push (multi-canonical-message-per-turn)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({ type: 'assistant', content: 'part 1', message_id: 'am-1' }))
    store.addMessage('sess-1', makeMessage({ type: 'assistant', content: 'part 2', message_id: 'am-2' }))

    const msgs = store.messagesBySession.get('sess-1')
    expect(msgs.length).toBe(2)
    expect(msgs.map(m => m.content)).toEqual(['part 1', 'part 2'])
  })

  it('rapid back-to-back turns: every canonical message appears exactly once and in order (#1945/#1949 regression guard)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    // Simulates several turns' canonical messages landing faster than a naive implementation
    // might settle each one — the single dedup-by-id rule needs no turn-stacking-aware logic.
    for (let i = 1; i <= 5; i++) {
      store.addMessage('sess-1', makeMessage({ type: 'assistant', content: `turn ${i}`, message_id: `am-${i}` }))
    }
    // A redelivery of an earlier turn (e.g. a reconnect replay) must not duplicate it.
    store.addMessage('sess-1', makeMessage({ type: 'assistant', content: 'turn 3', message_id: 'am-3' }))

    const msgs = store.messagesBySession.get('sess-1')
    expect(msgs.map(m => m.content)).toEqual(['turn 1', 'turn 2', 'turn 3', 'turn 4', 'turn 5'])
  })
})

describe('streamingPreviewBySession lifecycle (Issue #1955)', () => {
  beforeEach(() => {
    // message_stop/interrupt/restart flush or discard synchronously — suppressing rAF just
    // prevents an unrelated async flush from firing after a test's synchronous assertions.
    vi.stubGlobal('requestAnimationFrame', () => 1)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('message_start creates an active, empty preview', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-1'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))

    const preview = store.streamingPreviewBySession.get(SID)
    expect(preview.active).toBe(true)
    expect(preview.content).toBe('')
    expect(preview.thinking).toBe('')
  })

  it('content_block_delta (text and thinking) accumulates, and message_stop ends the caret but preserves content', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-2'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'thinking_delta', thinking: 'pondering' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 1, delta: { type: 'text_delta', text: 'hello ' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 1, delta: { type: 'text_delta', text: 'world' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    const preview = store.streamingPreviewBySession.get(SID)
    expect(preview.active).toBe(false)
    expect(preview.content).toBe('hello world')
    expect(preview.thinking).toBe('pondering')
  })

  it('a subsequent canonical addMessage() clears content/thinking but a genuinely new message_start starts fresh regardless of prior state', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-3'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'first turn' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    expect(store.streamingPreviewBySession.get(SID).content).toBe('first turn')

    store.addMessage(SID, makeMessage({ type: 'assistant', content: 'first turn', message_id: 'am-1' }))
    expect(store.streamingPreviewBySession.get(SID).content).toBe('')

    // A second, genuinely new turn's message_start resets cosmetic state regardless of
    // whatever the preview held before — no redelivery/overlap guarding needed since the
    // preview was never authoritative.
    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_2' } }))
    const preview = store.streamingPreviewBySession.get(SID)
    expect(preview.active).toBe(true)
    expect(preview.content).toBe('')
  })

  it('multi-canonical-message-per-turn: content clears after the first append, second append unaffected by the first append\'s leftover text', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-4'

    // One Anthropic turn dispatching a background Task can arrive as multiple separate
    // canonical frames (each with its own distinct backend message_id) while the raw stream
    // is still active — thinking-then-text split across two canonical appends, no message_stop
    // yet in between.
    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'thinking_delta', thinking: 'spawning agents' } }))
    store.addMessage(SID, makeMessage({ type: 'assistant', content: '', message_id: 'am-1', metadata: { thinking_content: 'spawning agents' } }))

    let preview = store.streamingPreviewBySession.get(SID)
    expect(preview.active).toBe(true) // still mid-stream — no message_stop yet
    expect(preview.thinking).toBe('') // cleared by the first canonical append

    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 1, delta: { type: 'text_delta', text: 'second frame text' } }))
    store.addMessage(SID, makeMessage({ type: 'assistant', content: 'second frame text', message_id: 'am-2' }))

    preview = store.streamingPreviewBySession.get(SID)
    expect(preview.content).toBe('') // cleared again by the second append, unaffected by the first
    expect(store.messagesBySession.get(SID).map(m => m.message_id)).toEqual(['am-1', 'am-2'])
  })

  it('level-triggered dismissal (review fix): canonical frame arriving BEFORE its own deltas still gets dismissed at message_stop, not left as a permanent leftover', async () => {
    // Reproduces the reported bug exactly: a single-frame turn where the canonical
    // AssistantMessage is processed before the streaming deltas (the canonical and delta
    // channels are delivered independently and can arrive in either order). The old
    // edge-triggered clear fired against an empty preview (no-op) and, with only one frame in
    // the turn, nothing ever cleared it again — the deltas then filled in the full text and it
    // stayed duplicated on screen indefinitely.
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-race'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    // Canonical frame lands FIRST, while the preview is still empty.
    store.addMessage(SID, makeMessage({ type: 'assistant', content: 'Yes', message_id: 'am-1' }))
    expect(store.streamingPreviewBySession.get(SID).content).toBe('') // clear no-op'd (already empty)

    // Deltas arrive AFTER the canonical — this is the ordering that used to leak. (rAF is
    // stubbed as a no-op in this suite, so the pending delta isn't applied to .content until
    // message_stop's synchronous flush — matching real behavior when message_stop follows the
    // delta closely, which is exactly the reported scenario.)
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'Yes' } }))
    expect(store.streamingPreviewBySession.get(SID).pendingText).toBe('Yes')

    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    // message_stop must catch the already-seen canonical and dismiss the preview itself —
    // there is no second canonical append coming to do it for a single-frame turn.
    const preview = store.streamingPreviewBySession.get(SID)
    expect(preview.content).toBe('')
    expect(preview.thinking).toBe('')
    expect(preview.active).toBe(false)
  })

  it('normal ordering (deltas then canonical) is unaffected by the level-triggered fix', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-normal-order'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'Yes' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    // Preview persists frozen after message_stop — canonicalSeen is still false, so nothing
    // clears it yet, matching the plan's swap-atomicity design (no gap before the canonical
    // bubble takes over).
    let preview = store.streamingPreviewBySession.get(SID)
    expect(preview.content).toBe('Yes')
    expect(preview.active).toBe(false)

    store.addMessage(SID, makeMessage({ type: 'assistant', content: 'Yes', message_id: 'am-1' }))

    preview = store.streamingPreviewBySession.get(SID)
    expect(preview.content).toBe('')
  })

  it('interrupt discards the preview', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-interrupt'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    expect(store.streamingPreviewBySession.get(SID)).toBeTruthy()

    store.addMessage(SID, makeMessage({ type: 'system', content: '', metadata: { subtype: 'interrupt' } }))

    expect(store.streamingPreviewBySession.get(SID)).toBeUndefined()
  })

  it('restart (client_launched) discards the preview', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-restart'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    expect(store.streamingPreviewBySession.get(SID)).toBeTruthy()

    store.addMessage(SID, makeMessage({ type: 'system', content: '', metadata: { subtype: 'client_launched' } }))

    expect(store.streamingPreviewBySession.get(SID)).toBeUndefined()
  })

  it('clearMessages discards the preview', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-clear'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    expect(store.streamingPreviewBySession.get(SID)).toBeTruthy()

    store.clearMessages(SID)

    expect(store.streamingPreviewBySession.get(SID)).toBeUndefined()
  })

  it('subagent narration (metadata.parent_tool_use_id set) does not clear the main turn\'s still-active preview (review fix)', async () => {
    // A background Task/Agent leg's own narration shares this session's id but belongs to a
    // different stream than the top-level turn the preview mirrors — its arrival must not
    // truncate the main turn's still-accumulating live text.
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-preview-subagent-narration'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'main-turn-1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'Main turn still typing' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))
    expect(store.streamingPreviewBySession.get(SID).content).toBe('Main turn still typing')

    // Subagent narration lands on the SAME session while the main turn's preview is still
    // frozen (awaiting its own canonical append) — must not clear it.
    store.addMessage(SID, makeMessage({
      type: 'assistant', content: 'Subagent narration text', message_id: 'subagent-narration-1',
      metadata: { parent_tool_use_id: 'toolu_task1' },
    }))

    expect(store.streamingPreviewBySession.get(SID).content).toBe('Main turn still typing')

    // The main turn's own canonical append still clears it as normal.
    store.addMessage(SID, makeMessage({ type: 'assistant', content: 'Main turn still typing', message_id: 'main-turn-1-canonical' }))
    expect(store.streamingPreviewBySession.get(SID).content).toBe('')
  })
})

describe('assistant_delta turn_id field (Issue #1987)', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', () => 1)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('handleAssistantDelta ignores the renamed turn_id field and the preview-to-final swap still yields exactly one message', async () => {
    // #1987 renamed the delta envelope's turn-identity key from message_id to turn_id.
    // handleAssistantDelta never read either key (the #1955 rewrite made the streaming
    // preview purely cosmetic, keyed only by sessionId) — this locks in that the extra
    // field is harmlessly ignored and the preview/final-message lifecycle is unaffected.
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-turn-id-1987'

    store.handleAssistantDelta(SID, { ...delta('message_start', SID, { message: { id: 'msg_1' } }), turn_id: 'msg_1' })
    store.handleAssistantDelta(SID, { ...delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'hello' } }), turn_id: 'msg_1' })
    store.handleAssistantDelta(SID, { ...delta('message_stop', SID, {}), turn_id: 'msg_1' })

    expect(store.streamingPreviewBySession.get(SID).content).toBe('hello')

    store.addMessage(SID, makeMessage({ type: 'assistant', content: 'hello', message_id: 'am-final-1987' }))

    const msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(1)
    expect(msgs[0].content).toBe('hello')
    expect(store.streamingPreviewBySession.get(SID).content).toBe('')
  })
})

describe('loadMessages reload-race (Issue #1955, replaces old #1945 tests)', () => {
  it('discards an active preview and shows exactly the freshly-loaded canonical history, nothing merged in', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-reload-race'

    vi.stubGlobal('requestAnimationFrame', () => 1)
    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'msg_1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'mid-stream text' } }))
    vi.unstubAllGlobals()

    expect(store.streamingPreviewBySession.get(SID)).toBeTruthy()

    apiMock.get.mockResolvedValueOnce({
      messages: [makeMessage({ type: 'assistant', content: 'from history', message_id: 'am-history' })],
      total_count: 1,
      has_more: false
    })

    const result = await store.loadMessages(SID)

    expect(store.streamingPreviewBySession.get(SID)).toBeUndefined()
    expect(result.messages.map(m => m.content)).toEqual(['from history'])
    expect(store.messagesBySession.get(SID).map(m => m.content)).toEqual(['from history'])
  })
})

describe('syncMessages clears the live preview on a recovered terminal (Issue #1955 review fix)', () => {
  it('a top-level assistant terminal recovered via stall-heal sync clears the frozen preview, same as addMessage() would', async () => {
    // syncMessages() merges directly into messagesBySession instead of routing through
    // addMessage() — it must still apply the same preview-clearing side effect for a genuine
    // top-level assistant terminal, or the frozen preview lingers indefinitely.
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-sync-preview-clear'

    store.addMessage(SID, makeMessage({ content: 'seed', timestamp: 1700000000 }))
    apiMock.get.mockReset()

    vi.stubGlobal('requestAnimationFrame', () => 1)
    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'stall-turn-1' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'Recovered via stall-heal' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))
    vi.unstubAllGlobals()
    expect(store.streamingPreviewBySession.get(SID).content).toBe('Recovered via stall-heal')

    apiMock.get.mockResolvedValueOnce({
      messages: [makeMessage({
        type: 'assistant', content: 'Recovered via stall-heal', timestamp: 1700000100, message_id: 'stall-turn-1-canonical',
      })],
      total_count: 2,
      has_more: false
    })

    await store.syncMessages(SID)

    expect(store.streamingPreviewBySession.get(SID).content).toBe('')
  })

  it('does not touch the preview when sync finds nothing new (no false-positive clear during a healthy ongoing stream)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-sync-preview-noop'

    store.addMessage(SID, makeMessage({ content: 'seed', timestamp: 1700000000 }))
    apiMock.get.mockReset()

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: 'ongoing-turn-1' } }))
    expect(store.streamingPreviewBySession.get(SID).active).toBe(true)

    apiMock.get.mockResolvedValueOnce({ messages: [], total_count: 1, has_more: false })

    await store.syncMessages(SID)

    // A stall-check that finds nothing new must not disturb an unrelated, still-healthy stream.
    expect(store.streamingPreviewBySession.get(SID).active).toBe(true)
  })
})

describe('applyTaskLifecycleFrame — task_id-first subagent tracking (#1746 stage: subagents / #1765)', () => {
  it('task_started appends a new leg keyed by task_id', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-A',
      tool_use_id: 'toolu_launch',
      description: 'alpha: explore the repo',
    }, 100)

    const entry = store.getTaskLegEntry('task-A')
    expect(entry.legs).toHaveLength(1)
    expect(entry.legs[0]).toMatchObject({
      tool_use_id: 'toolu_launch',
      description: 'alpha: explore the repo',
      status: 'running',
      started_at: 100,
    })
    expect(store.getTaskIdForLaunchToolUse('toolu_launch')).toBe('task-A')
  })

  it('a resume (second task_started for the same task_id) appends a second leg, not overwriting the first', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-B', tool_use_id: 'toolu_1' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_notification', { task_id: 'task-B', status: 'stopped' }, 150)
    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-B', tool_use_id: 'toolu_2' }, 200)

    const entry = store.getTaskLegEntry('task-B')
    expect(entry.legs).toHaveLength(2)
    expect(entry.legs[0]).toMatchObject({ tool_use_id: 'toolu_1', status: 'stopped' })
    expect(entry.legs[1]).toMatchObject({ tool_use_id: 'toolu_2', status: 'running' })
    // Both legs' own tool_use_ids resolve to the same task_id.
    expect(store.getTaskIdForLaunchToolUse('toolu_1')).toBe('task-B')
    expect(store.getTaskIdForLaunchToolUse('toolu_2')).toBe('task-B')
  })

  it('task_notification summary is captured as the leg result; task_updated never sets one', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-R', tool_use_id: 'toolu_r' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_notification', {
      task_id: 'task-R', status: 'completed', summary: 'The verses are sent, the work is through.',
    }, 150)

    expect(store.getTaskLegEntry('task-R').legs[0].result).toBe('The verses are sent, the work is through.')

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-S', tool_use_id: 'toolu_s' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_updated', { task_id: 'task-S', status: 'killed' }, 150)

    expect(store.getTaskLegEntry('task-S').legs[0].result).toBeUndefined()
  })

  it('task_progress bumps last_progress_at on the latest leg only, and is a no-op once terminal', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-C', tool_use_id: 'toolu_c' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_progress', { task_id: 'task-C' }, 120)
    expect(store.getTaskLegEntry('task-C').legs[0].last_progress_at).toBe(120)

    store.applyTaskLifecycleFrame('sess-1', 'task_notification', { task_id: 'task-C', status: 'completed' }, 130)
    store.applyTaskLifecycleFrame('sess-1', 'task_progress', { task_id: 'task-C' }, 999)
    // Progress after termination must not resurrect the leg or move its timestamp.
    expect(store.getTaskLegEntry('task-C').legs[0].last_progress_at).toBe(120)
    expect(store.getTaskLegEntry('task-C').legs[0].ended_at).toBe(130)
  })

  it('first-terminal-wins: a second terminal frame does not override the first', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-D', tool_use_id: 'toolu_d' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_notification', { task_id: 'task-D', status: 'completed' }, 200)
    store.applyTaskLifecycleFrame('sess-1', 'task_updated', { task_id: 'task-D', status: 'killed' }, 300)

    const leg = store.getTaskLegEntry('task-D').legs[0]
    expect(leg.status).toBe('completed')
    expect(leg.ended_at).toBe(200)
  })

  it('task_updated with status=killed normalizes to "stopped" for display', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-E', tool_use_id: 'toolu_e' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_updated', { task_id: 'task-E', status: 'killed' }, 200)

    expect(store.getTaskLegEntry('task-E').legs[0].status).toBe('stopped')
  })

  it('task_updated reads status from patch when top-level status is absent', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-F', tool_use_id: 'toolu_f' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_updated', { task_id: 'task-F', patch: { status: 'failed' } }, 200)

    expect(store.getTaskLegEntry('task-F').legs[0].status).toBe('failed')
  })

  it('a frame with no task_id or an unrecognized subtype is ignored', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { tool_use_id: 'toolu_x' }, 100)
    expect(store.getTaskLegEntry('task-missing')).toBeNull()

    store.applyTaskLifecycleFrame('sess-1', 'not_a_real_subtype', { task_id: 'task-G' }, 100)
    expect(store.getTaskLegEntry('task-G')).toBeNull()
  })

  it('issue #1771: a local_bash task_started produces no leg and no launch-tool-use mapping', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-bash',
      tool_use_id: 'toolu_bash',
      task_type: 'local_bash',
    }, 100)

    expect(store.getTaskLegEntry('task-bash')).toBeNull()
    expect(store.getTaskIdForLaunchToolUse('toolu_bash')).toBeNull()
  })

  it('issue #1771: a task_started with no task_type still registers normally (backward compat)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-notype',
      tool_use_id: 'toolu_notype',
    }, 100)

    const entry = store.getTaskLegEntry('task-notype')
    expect(entry.legs).toHaveLength(1)
    expect(store.getTaskIdForLaunchToolUse('toolu_notype')).toBe('task-notype')
  })

  it('hydrateBackgroundAgents seeds legs from the backend snapshot without replaying frames', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    apiMock.get.mockResolvedValue({
      session_id: 'sess-1',
      agents: [
        {
          task_id: 'task-H',
          legs: [
            { tool_use_id: 'toolu_h1', description: 'first leg', started_at: 10, last_progress_at: 20, ended_at: 30, status: 'completed' },
            { tool_use_id: 'toolu_h2', description: 'resumed leg', started_at: 40, last_progress_at: 40, ended_at: null, status: 'running' },
          ],
        },
      ],
    })

    await store.hydrateBackgroundAgents('sess-1')

    const entry = store.getTaskLegEntry('task-H')
    expect(entry.legs).toHaveLength(2)
    expect(store.getTaskIdForLaunchToolUse('toolu_h1')).toBe('task-H')
    expect(store.getTaskIdForLaunchToolUse('toolu_h2')).toBe('task-H')
  })

})

describe('leg grouping by timestamp window — resume via SendMessage (#1746 follow-up, real repro)', () => {
  // Reproduces the real Bard-E/F repro: a subagent is resumed not via a fresh Task/Agent call,
  // but via the main session calling SendMessage(to: "<agent name>") — whose task_started frame
  // reports the SendMessage call's OWN tool_use_id, not a Task/Agent call's. Confirmed from the
  // real data: parent_tool_use_id on ALL of a subagent's child activity (original run AND every
  // resume) stays pinned to the very first leg's own launch tool_use_id — never to the resume
  // trigger's id — so grouping must resolve by WHEN activity happened, not by an exact
  // parent_tool_use_id match against a specific leg.
  const SID = 'sess-bardE'
  const TASK_ID = 'a35b5c50d38dad9b4' // real task_id from the repro
  const ROOT_TOOL_USE_ID = 'toolu_01BrQe3UjRSfdWZ3F4rnX5wN' // real leg-0 launch id (Task/Agent)
  const RESUME_TOOL_USE_ID = 'toolu_01DHhviQiSYa2YH5waCU3YwH' // real resume trigger (SendMessage)

  function setupTwoLegs(store) {
    // Leg 0: original launch (Task/Agent), runs, then completes.
    store.applyTaskLifecycleFrame(SID, 'task_started', { task_id: TASK_ID, tool_use_id: ROOT_TOOL_USE_ID, description: 'Bard-E verse sequence' }, 100)
    store.applyTaskLifecycleFrame(SID, 'task_notification', { task_id: TASK_ID, status: 'completed' }, 150)
    // Leg 1: resumed via SendMessage(to:"Bard-E") — a DIFFERENT tool_use_id, same task_id.
    store.applyTaskLifecycleFrame(SID, 'task_started', { task_id: TASK_ID, tool_use_id: RESUME_TOOL_USE_ID, description: 'Bard-E verse sequence' }, 200)
  }

  it('computeSubagentAnchorsBySegment recognizes a SendMessage resume trigger once resolved via the store, not by name', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { computeSubagentAnchorsBySegment } = await import('@/utils/subagentAnchors')
    const store = useMessageStore()
    setupTwoLegs(store)

    const isLaunchAnchor = (tc) => tc.name === 'Task' || tc.name === 'Agent' || !!store.getTaskIdForLaunchToolUse(tc.id)

    const segment = [
      { id: RESUME_TOOL_USE_ID, name: 'SendMessage', input: { to: 'Bard-E' } }, // resolves via store
      { id: 'toolu_unrelated_sendmessage', name: 'SendMessage', input: { to: 'Bard-F' } }, // does NOT resolve — not a real anchor
    ]
    const result = computeSubagentAnchorsBySegment([segment], isLaunchAnchor)
    expect(result[0].map(a => a.id)).toEqual([RESUME_TOOL_USE_ID])
  })

  it('childToolCallsForLeg buckets child tool calls by which leg was active at the time, not by parent_tool_use_id match', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    setupTwoLegs(store)

    // All child tool calls share the SAME parent_tool_use_id (the root/leg-0 launch id) —
    // confirmed real behavior — but happen at different times relative to each leg's window.
    store.handleToolCall(SID, { tool_use_id: 'toolu_leg0_tool', name: 'ToolSearch', input: {}, status: 'completed', parent_tool_use_id: ROOT_TOOL_USE_ID, created_at: 120 }) // during leg 0's window [100,200)
    store.handleToolCall(SID, { tool_use_id: 'toolu_leg1_tool', name: 'SendMessage', input: { to: 'main' }, status: 'completed', parent_tool_use_id: ROOT_TOOL_USE_ID, created_at: 250 }) // during leg 1's window [200, inf)

    const leg0Tools = store.childToolCallsForLeg(SID, TASK_ID, 0)
    const leg1Tools = store.childToolCallsForLeg(SID, TASK_ID, 1)

    expect(leg0Tools.map(t => t.id)).toEqual(['toolu_leg0_tool'])
    expect(leg1Tools.map(t => t.id)).toEqual(['toolu_leg1_tool'])
  })

  it('narration (addMessage routing) attaches to the leg that was active at the narration message\'s own timestamp', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    setupTwoLegs(store)

    // Narration during leg 0's window.
    store.addMessage(SID, {
      type: 'assistant',
      content: 'Working on the first haiku.',
      timestamp: 130,
      metadata: { parent_tool_use_id: ROOT_TOOL_USE_ID },
    })
    // Narration during leg 1's window (after the resume) — same parent_tool_use_id as above.
    store.addMessage(SID, {
      type: 'assistant',
      content: 'Working on the follow-up haiku.',
      timestamp: 260,
      metadata: { parent_tool_use_id: ROOT_TOOL_USE_ID },
    })

    const leg0Narration = store.narrationForLeg(TASK_ID, 0)
    const leg1Narration = store.narrationForLeg(TASK_ID, 1)

    expect(leg0Narration.map(m => m.content)).toEqual(['Working on the first haiku.'])
    expect(leg1Narration.map(m => m.content)).toEqual(['Working on the follow-up haiku.'])
  })
})

describe('applyDisplayMetadata status-regression guard (Issue #2007)', () => {
  // Issue #2007 (Gap B): fixing the backend DisplayProjection no-op bug activated a
  // previously-dead frontend code path — display.tool_states was always {} before, so
  // this for-loop in applyDisplayMetadata() never actually ran. DisplayProjection only
  // ever tracks pending -> completed/failed on the live path (it never reports the live
  // awaiting_permission/running states the dedicated #324 ToolCallUpdate pipeline already
  // sets), and its per-session snapshot is cumulative — every tool ever seen in the
  // session, not a delta. Without a regression guard, any later message in the session
  // re-attaches a stale 'pending' entry for a still-in-progress tool and silently reverts
  // its status. SkillToolHandler.vue/SlashCommandToolHandler.vue read toolCall.status
  // directly (bypassing the useToolStatus composable that masks this for most other tool
  // cards via backendStatus priority), so this is user-visible for those tool types.
  it('does not revert an executing tool call to pending when a later unrelated message carries a stale cumulative display snapshot', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    // Real #324 ToolCallUpdate pipeline sets the Skill tool call to 'executing'.
    store.handleToolCall('sess-1', {
      tool_use_id: 'toolu_skill_1',
      name: 'Skill',
      input: { command: 'my-skill' },
      status: 'running',
    })
    expect(store.toolCallsBySession.get('sess-1').find(tc => tc.id === 'toolu_skill_1').status).toBe('executing')

    // A later, unrelated message in the session carries DisplayProjection's cumulative
    // snapshot, which still shows the still-in-progress Skill tool as 'pending' (it has
    // no live 'executing' state of its own).
    store.addMessage('sess-1', makeMessage({
      type: 'assistant',
      content: 'unrelated text',
      metadata: {
        display: {
          tool_states: {
            toolu_skill_1: { state: 'pending', visible: true, collapsed: false, style: 'default' }
          },
          orphaned_tools: [],
          linked_permissions: {}
        }
      }
    }))

    const toolCall = store.toolCallsBySession.get('sess-1').find(tc => tc.id === 'toolu_skill_1')
    expect(toolCall.status).toBe('executing')
  })

  it('does not revert a permission_required tool call to pending from a stale cumulative display snapshot', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'toolu_slash_1',
      name: 'SlashCommand',
      input: { command: '/deploy' },
      status: 'awaiting_permission',
    })
    expect(store.toolCallsBySession.get('sess-1').find(tc => tc.id === 'toolu_slash_1').status).toBe('permission_required')

    store.addMessage('sess-1', makeMessage({
      type: 'assistant',
      content: 'unrelated text',
      metadata: {
        display: {
          tool_states: {
            toolu_slash_1: { state: 'pending', visible: true, collapsed: false, style: 'default' }
          },
          orphaned_tools: [],
          linked_permissions: {}
        }
      }
    }))

    const toolCall = store.toolCallsBySession.get('sess-1').find(tc => tc.id === 'toolu_slash_1')
    expect(toolCall.status).toBe('permission_required')
  })

  it('still applies a forward transition (pending -> completed) from the display snapshot', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'toolu_read_1',
      name: 'Read',
      input: { file_path: '/x.py' },
      status: 'pending',
    })
    expect(store.toolCallsBySession.get('sess-1').find(tc => tc.id === 'toolu_read_1').status).toBe('pending')

    store.addMessage('sess-1', makeMessage({
      type: 'user',
      content: 'tool result',
      metadata: {
        display: {
          tool_states: {
            toolu_read_1: { state: 'completed', visible: true, collapsed: false, style: 'success' }
          },
          orphaned_tools: [],
          linked_permissions: {}
        }
      }
    }))

    const toolCall = store.toolCallsBySession.get('sess-1').find(tc => tc.id === 'toolu_read_1')
    expect(toolCall.status).toBe('completed')
  })
})

describe('openPermissionsForSession (#1746 stage: permissions)', () => {
  it('returns a main-session-only permission with taskId null', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-main',
      name: 'Edit',
      input: { path: '/tmp/f' },
      status: 'awaiting_permission',
      request_id: 'req-main',
    })

    const perms = store.openPermissionsForSession('sess-1')
    expect(perms).toHaveLength(1)
    expect(perms[0]).toMatchObject({
      requestId: 'req-main',
      taskId: null,
      legIndex: null,
      isSubagent: false,
      label: 'Main session',
    })
  })

  it('returns a subagent-only permission resolved to its running leg', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-1', tool_use_id: 'launch-1', description: 'Fix the failing test',
    }, 100)
    store.handleToolCall('sess-1', {
      tool_use_id: 'child-1',
      name: 'Bash',
      input: { command: 'pytest' },
      status: 'awaiting_permission',
      request_id: 'req-sub',
      parent_tool_use_id: 'launch-1',
    })

    const perms = store.openPermissionsForSession('sess-1')
    expect(perms).toHaveLength(1)
    expect(perms[0]).toMatchObject({
      requestId: 'req-sub',
      taskId: 'task-1',
      legIndex: 0,
      isSubagent: true,
      label: 'Fix the failing test',
    })
  })

  it('returns both a concurrent subagent and main-session permission', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-main',
      name: 'Write',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-main',
    })
    store.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-1', tool_use_id: 'launch-1', description: 'Refactor the parser',
    }, 100)
    store.handleToolCall('sess-1', {
      tool_use_id: 'child-1',
      name: 'Edit',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-sub',
      parent_tool_use_id: 'launch-1',
    })

    const perms = store.openPermissionsForSession('sess-1')
    expect(perms).toHaveLength(2)
    expect(perms.map(p => p.requestId).sort()).toEqual(['req-main', 'req-sub'])
  })

  it('resolving one permission leaves the other open (mirrors the backend per-session-count bug, #6.1)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const { useSessionStore } = await import('@/stores/session')
    const store = useMessageStore()
    const sessionStore = useSessionStore()
    sessionStore.currentSessionId = 'sess-1'

    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-1', tool_use_id: 'launch-1' }, 100)
    store.applyTaskLifecycleFrame('sess-1', 'task_started', { task_id: 'task-2', tool_use_id: 'launch-2' }, 100)
    store.handleToolCall('sess-1', {
      tool_use_id: 'child-1', name: 'Bash', input: {}, status: 'awaiting_permission',
      request_id: 'req-1', parent_tool_use_id: 'launch-1',
    })
    store.handleToolCall('sess-1', {
      tool_use_id: 'child-2', name: 'Edit', input: {}, status: 'awaiting_permission',
      request_id: 'req-2', parent_tool_use_id: 'launch-2',
    })

    expect(store.openPermissionsForSession('sess-1')).toHaveLength(2)

    store.handlePermissionResponse('sess-1', { request_id: 'req-1', decision: 'allow' })

    const remaining = store.openPermissionsForSession('sess-1')
    expect(remaining).toHaveLength(1)
    expect(remaining[0].requestId).toBe('req-2')
  })

  it('falls back to the most recent leg when no leg is currently running (issue_1746_no_running_leg_fallback)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    // Leg goes terminal (task_notification) before its own child permission resolves — an
    // edge case the backend's pause/resume gate normally prevents, but the frontend must
    // still degrade gracefully rather than leaving legIndex null (which would silently break
    // PermissionQueue's "view in context").
    store.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-1', tool_use_id: 'launch-1', description: 'Stale leg',
    }, 100)
    store.handleToolCall('sess-1', {
      tool_use_id: 'child-1', name: 'Bash', input: {}, status: 'awaiting_permission',
      request_id: 'req-1', parent_tool_use_id: 'launch-1',
    })
    store.applyTaskLifecycleFrame('sess-1', 'task_notification', { task_id: 'task-1', status: 'completed' }, 150)

    const perms = store.openPermissionsForSession('sess-1')
    expect(perms).toHaveLength(1)
    expect(perms[0]).toMatchObject({ taskId: 'task-1', legIndex: 0, label: 'Stale leg' })
  })

  // Issue #1748 (stage: windowing): expandedTimelineTool/expandedTimelineToolAutoPermission
  // back ActivityTimeline's tool-detail-expand state so it survives a virtualized row's
  // unmount/remount cycle (see ActivityTimeline.vue). Covers the get/set contract and the
  // auto-permission flag the review fix added after the local-ref version broke auto-collapse
  // across a remount.
  describe('expandedTimelineTool (#1748 stage: windowing)', () => {
    it('get/set round-trips and null clears the entry', async () => {
      const { useMessageStore } = await import('@/stores/message')
      const store = useMessageStore()

      expect(store.getExpandedTimelineTool('msg-1')).toBeNull()
      store.setExpandedTimelineTool('msg-1', 'tool-1')
      expect(store.getExpandedTimelineTool('msg-1')).toBe('tool-1')
      store.setExpandedTimelineTool('msg-1', null)
      expect(store.getExpandedTimelineTool('msg-1')).toBeNull()
    })

    it('tracks the autoPermission flag separately from the expanded tool id', async () => {
      const { useMessageStore } = await import('@/stores/message')
      const store = useMessageStore()

      store.setExpandedTimelineTool('msg-1', 'tool-1', { autoPermission: true })
      expect(store.isExpandedTimelineToolAutoPermission('msg-1')).toBe(true)

      // A manual expand (no autoPermission) clears the flag even though a tool stays expanded.
      store.setExpandedTimelineTool('msg-1', 'tool-2')
      expect(store.getExpandedTimelineTool('msg-1')).toBe('tool-2')
      expect(store.isExpandedTimelineToolAutoPermission('msg-1')).toBe(false)
    })

    it('clearMessages prunes entries scoped to the reset session, leaving other sessions intact', async () => {
      const { useMessageStore } = await import('@/stores/message')
      const store = useMessageStore()

      store.messagesBySession.set('sess-1', [
        makeMessage({ id: 'msg-1', session_id: 'sess-1' })
      ])
      store.messagesBySession.set('sess-2', [
        makeMessage({ id: 'msg-2', session_id: 'sess-2' })
      ])
      store.setExpandedTimelineTool('msg-1', 'tool-1')
      store.setExpandedTimelineTool('msg-2', 'tool-2')

      store.clearMessages('sess-1')

      expect(store.getExpandedTimelineTool('msg-1')).toBeNull()
      expect(store.getExpandedTimelineTool('msg-2')).toBe('tool-2')
    })

    it('clearMessages prunes SubagentLegTranscript-style run-suffixed scope keys too', async () => {
      const { useMessageStore } = await import('@/stores/message')
      const store = useMessageStore()

      store.messagesBySession.set('sess-1', [
        makeMessage({
          id: 'msg-1',
          session_id: 'sess-1',
          type: 'assistant',
          metadata: { tool_uses: [{ id: 'toolu_1', name: 'Bash', input: {} }] }
        })
      ])
      store.setExpandedTimelineTool('toolu_1-run-0', 'toolu_1')

      store.clearMessages('sess-1')

      expect(store.getExpandedTimelineTool('toolu_1-run-0')).toBeNull()
    })
  })

  // Issue #1748 (stage: windowing) review fix: same remount-survival treatment as
  // expandedTimelineTool above, for ThinkingBlock's expand/collapse toggle.
  describe('thinkingBlockExpanded (#1748 stage: windowing)', () => {
    it('toggles and defaults to collapsed', async () => {
      const { useMessageStore } = await import('@/stores/message')
      const store = useMessageStore()

      expect(store.isThinkingBlockExpanded('msg-1')).toBe(false)
      store.toggleThinkingBlockExpanded('msg-1')
      expect(store.isThinkingBlockExpanded('msg-1')).toBe(true)
      store.toggleThinkingBlockExpanded('msg-1')
      expect(store.isThinkingBlockExpanded('msg-1')).toBe(false)
    })

    it('clearMessages prunes entries scoped to the reset session', async () => {
      const { useMessageStore } = await import('@/stores/message')
      const store = useMessageStore()

      store.messagesBySession.set('sess-1', [makeMessage({ id: 'msg-1', session_id: 'sess-1' })])
      store.messagesBySession.set('sess-2', [makeMessage({ id: 'msg-2', session_id: 'sess-2' })])
      store.toggleThinkingBlockExpanded('msg-1')
      store.toggleThinkingBlockExpanded('msg-2')

      store.clearMessages('sess-1')

      expect(store.isThinkingBlockExpanded('msg-1')).toBe(false)
      expect(store.isThinkingBlockExpanded('msg-2')).toBe(true)
    })
  })
})
