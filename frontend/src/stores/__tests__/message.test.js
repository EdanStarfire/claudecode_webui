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
    expect(store.isToolUseOrphaned('sess-1', 'use-1')).toBe(true)
  })
})

// Helpers shared by streaming merge tests
function delta(type, sessionId, event) {
  return { uuid: 'env-' + Math.random(), event: { type, ...event } }
}

describe('streaming merge — collect-and-replace (#1601)', () => {
  beforeEach(() => {
    // Prevent rAF callbacks from firing during synchronous test steps.
    // message_stop flushes pending deltas directly, so suppressing rAF is safe.
    vi.stubGlobal('requestAnimationFrame', () => 1)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('Case A: single terminal AM produces one bubble with terminal content', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-a'
    const ANTHROPIC_ID = 'msg_anthropic_A'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: ANTHROPIC_ID } }))
    store.handleAssistantDelta(SID, delta('content_block_start', SID, { index: 0, content_block: { type: 'text' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'hello ' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'world' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: '!' } }))

    // Terminal AssistantMessage arrives before message_stop
    store.addMessage(SID, {
      type: 'assistant',
      message_id: 'am-a-uuid',
      content: 'hello world!',
      metadata: { message_id: ANTHROPIC_ID, has_thinking: false, thinking_content: '', has_tool_uses: false, tool_uses: [] },
    })

    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    const msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(1)
    expect(msgs[0].content).toBe('hello world!')
    expect(msgs[0].streaming).toBe(false)
  })

  it('Case B: multiple intermediate AMs produce separate bubbles preserving order', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-b'
    const ANTHROPIC_ID = 'msg_anthropic_B'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: ANTHROPIC_ID } }))

    // Thinking block deltas
    store.handleAssistantDelta(SID, delta('content_block_start', SID, { index: 0, content_block: { type: 'thinking' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'thinking_delta', thinking: 'thinking...' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'thinking_delta', thinking: ' more' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'thinking_delta', thinking: ' done' } }))

    // First terminal AM: thinking-only content block
    store.addMessage(SID, {
      type: 'assistant',
      message_id: 'am-b-uuid-1',
      content: '',
      metadata: {
        message_id: ANTHROPIC_ID,
        has_thinking: true,
        thinking_content: 'thinking... more done',
        has_tool_uses: false,
        tool_uses: [],
      },
    })

    // Text + tool_use block deltas
    store.handleAssistantDelta(SID, delta('content_block_start', SID, { index: 1, content_block: { type: 'text' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 1, delta: { type: 'text_delta', text: 'result' } }))
    store.handleAssistantDelta(SID, delta('content_block_start', SID, { index: 2, content_block: { type: 'tool_use', name: 'spawn_minion' } }))

    // Second terminal AM: text + tool_use content blocks
    store.addMessage(SID, {
      type: 'assistant',
      message_id: 'am-b-uuid-2',
      content: 'result',
      metadata: {
        message_id: ANTHROPIC_ID,
        has_thinking: false,
        thinking_content: '',
        has_tool_uses: true,
        tool_uses: [{ name: 'spawn_minion', id: 'tool-use-1', input: {} }],
      },
    })

    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    const msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(2)
    // First bubble: thinking AM
    expect(msgs[0].metadata.has_thinking).toBe(true)
    expect(msgs[0].metadata.thinking_content).toContain('thinking')
    expect(msgs[0].streaming).toBe(false)
    // Second bubble: text+tool AM
    expect(msgs[1].metadata.has_tool_uses).toBe(true)
    expect(msgs[1].metadata.tool_uses.length).toBe(1)
    expect(msgs[1].metadata.tool_uses[0].name).toBe('spawn_minion')
    expect(msgs[1].streaming).toBe(false)
  })

  it('Case C: message_stop before terminal AM — terminal merges into finalized placeholder (Fix A #1626)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-c'
    const ANTHROPIC_ID = 'msg_anthropic_C'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: ANTHROPIC_ID } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'partial ' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'response' } }))

    // message_stop fires with no collected terminal AM
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    const msgsAfterStop = store.messagesBySession.get(SID)
    expect(msgsAfterStop.length).toBe(1)
    expect(msgsAfterStop[0].content).toBe('partial response')
    expect(msgsAfterStop[0].streaming).toBe(false)

    // Late-arriving terminal AM — Fix A merges it into the finalized placeholder.
    // The terminal's content wins (authoritative full response over streamed approximation).
    store.addMessage(SID, {
      type: 'assistant',
      message_id: 'am-c-uuid',
      content: 'late content',
      metadata: { message_id: ANTHROPIC_ID, has_thinking: false, thinking_content: '', has_tool_uses: false, tool_uses: [] },
    })

    const msgsAfterLate = store.messagesBySession.get(SID)
    expect(msgsAfterLate.length).toBe(1)
    expect(msgsAfterLate[0].content).toBe('late content')
    expect(msgsAfterLate[0].streaming).toBe(false)
  })
})

describe('terminal-AM after finalized placeholder — Fix A (#1626)', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', () => 1)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('terminal AM with tool_uses after message_stop merges into finalized placeholder', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-1626-a'
    const ANTHROPIC_ID = 'msg_1626_a'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: ANTHROPIC_ID } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'I will edit the file.' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    // Verify: placeholder is finalized with no tool_uses
    let msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(1)
    expect(msgs[0].streaming).toBe(false)
    expect(msgs[0].metadata?.tool_uses?.length || 0).toBe(0)

    // Terminal AM arrives after message_stop with tool_uses populated (the bug scenario)
    store.addMessage(SID, {
      type: 'assistant',
      message_id: 'am-1626-a-uuid',
      content: 'I will edit the file.',
      metadata: {
        message_id: ANTHROPIC_ID,
        has_tool_uses: true,
        tool_uses: [{ id: 'toolA', name: 'Edit', input: { file_path: '/tmp/foo.txt' } }],
      },
    })

    // Fix A: merged — still one message, now with tool_uses populated
    msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(1)
    expect(msgs[0].streaming).toBe(false)
    expect(msgs[0].metadata.tool_uses).toHaveLength(1)
    expect(msgs[0].metadata.tool_uses[0].id).toBe('toolA')
  })

  it('terminal AM during active stream still defers to buffer (regression guard)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-1626-b'
    const ANTHROPIC_ID = 'msg_1626_b'

    // Stream is active
    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: ANTHROPIC_ID } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'text_delta', text: 'partial' } }))

    // Terminal AM arrives WHILE stream is active — should be deferred, not merged yet
    store.addMessage(SID, {
      type: 'assistant',
      message_id: 'am-1626-b-uuid',
      content: 'I will edit the file.',
      metadata: {
        message_id: ANTHROPIC_ID,
        has_tool_uses: true,
        tool_uses: [{ id: 'toolB', name: 'Edit', input: {} }],
      },
    })

    // Placeholder is still streaming with no tool_uses yet
    let msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(1)
    expect(msgs[0].streaming).toBe(true)
    expect(msgs[0].metadata?.tool_uses?.length || 0).toBe(0)

    // After message_stop, the terminal is spliced in
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))
    msgs = store.messagesBySession.get(SID)
    expect(msgs.length).toBe(1)
    expect(msgs[0].streaming).toBe(false)
    expect(msgs[0].metadata.tool_uses).toHaveLength(1)
    expect(msgs[0].metadata.tool_uses[0].id).toBe('toolB')
  })

  it('same terminal arriving twice after merge does not create a duplicate bubble', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-1626-c'
    const ANTHROPIC_ID = 'msg_1626_c'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: ANTHROPIC_ID } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    const terminal = {
      type: 'assistant',
      message_id: 'am-1626-c-uuid',
      content: 'response',
      metadata: {
        message_id: ANTHROPIC_ID,
        has_tool_uses: true,
        tool_uses: [{ id: 'toolC', name: 'Edit', input: {} }],
      },
    }

    store.addMessage(SID, terminal)
    expect(store.messagesBySession.get(SID).length).toBe(1)

    // Second arrival of the same terminal (reconnect/replay scenario) — must not duplicate
    store.addMessage(SID, terminal)
    expect(store.messagesBySession.get(SID).length).toBe(1)
  })
})
