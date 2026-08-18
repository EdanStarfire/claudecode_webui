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

  it('handleToolCall captures messageId on create and on update (#1694)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-1',
      name: 'Bash',
      input: { command: 'ls' },
      status: 'running',
      message_id: 'msg-abc'
    })

    let calls = store.toolCallsBySession.get('sess-1')
    expect(calls[0].messageId).toBe('msg-abc')

    store.handleToolCall('sess-1', {
      tool_use_id: 'use-2',
      name: 'Edit',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-2'
      // no message_id — legacy payload
    })

    calls = store.toolCallsBySession.get('sess-1')
    expect(calls[1].messageId).toBeNull()

    // Update branch: a later event for use-2 carries message_id
    store.handleToolCall('sess-1', {
      tool_use_id: 'use-2',
      name: 'Edit',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-2',
      message_id: 'msg-def'
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
    expect(store.isToolUseOrphaned('sess-1', 'use-1')).toBe(true)
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

describe('addMessage metadata accumulation across same-message_id frames (#1765 confirmed root cause)', () => {
  // Reproduces a real user-provided repro (messages.jsonl from a live session): a single
  // Anthropic assistant message (one message_id) dispatching a run_in_background Task/Agent
  // call arrives at the store as MULTIPLE separate backend frames sharing that message_id —
  // thinking, then text, then a tool_use for agent A, then (seconds later, after agent A's own
  // nested activity) a second tool_use for agent B. Each frame's own metadata reflects only
  // that frame's own content blocks. The old merge (`{...existing, ...message}`) shallow-
  // overwrote `metadata` wholesale, so agent B's frame deleted agent A's tool_use from
  // metadata.tool_uses the instant it arrived — not a rendering/key issue, the underlying data
  // was gone. This is what a live viewer saw as "the second subagent's card evicts the first's".
  it('accumulates tool_uses from two later frames sharing the same message_id (both agents survive)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-1765-repro'
    const MID = 'msg_011Ce6SkEyufGbHMpijsa5ij' // real message_id from the repro

    // A real stream for this message_id: thinking + text tokens, then message_stop finalizes
    // the placeholder (matches the "terminal AM after message_stop" pattern used elsewhere in
    // this file — see 'terminal AM with tool_uses after message_stop merges into finalized
    // placeholder' above).
    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: MID } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 0, delta: { type: 'thinking_delta', thinking: 'Spawning two background agents.' } }))
    store.handleAssistantDelta(SID, delta('content_block_delta', SID, { index: 1, delta: { type: 'text_delta', text: 'Recreating it identically.' } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    let stored = store.messagesBySession.get(SID).find(m => m.message_id === MID)
    expect(stored.streaming).toBe(false)
    expect(stored.content).toBe('Recreating it identically.')

    // Frame: tool_use for agent A (Bard-C), same message_id, arriving after message_stop.
    store.addMessage(SID, {
      type: 'assistant',
      content: '',
      metadata: { message_id: MID, tool_uses: [{ id: 'toolu_bardC', name: 'Agent', input: { name: 'Bard-C' } }], has_tool_uses: true },
    })

    stored = store.messagesBySession.get(SID).find(m => m.message_id === MID)
    expect(stored.metadata.tool_uses.map(t => t.id)).toEqual(['toolu_bardC'])
    expect(stored.content).toBe('Recreating it identically.') // text preserved through the tool_use-only frame

    // Frame (seconds later, after agent A's own nested subagent turns — unrelated message_ids
    // in between, not shown here): tool_use for agent B (Bard-D), SAME message_id.
    store.addMessage(SID, {
      type: 'assistant',
      content: '',
      metadata: { message_id: MID, tool_uses: [{ id: 'toolu_bardD', name: 'Agent', input: { name: 'Bard-D' } }], has_tool_uses: true },
    })

    stored = store.messagesBySession.get(SID).find(m => m.message_id === MID)
    // Both agents' tool_uses must survive — this is the exact assertion that fails under the
    // old `{...existing, ...message}` overwrite (it would leave only toolu_bardD).
    expect(stored.metadata.tool_uses.map(t => t.id).sort()).toEqual(['toolu_bardC', 'toolu_bardD'])
    expect(stored.content).toBe('Recreating it identically.')
    expect(store.messagesBySession.get(SID)).toHaveLength(1) // still one bubble, not two
  })

  it('does not duplicate a tool_use if the same message_id/tool_use_id pair is redelivered', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()
    const SID = 'sess-1765-dup'
    const MID = 'msg-dup-test'

    store.handleAssistantDelta(SID, delta('message_start', SID, { message: { id: MID } }))
    store.handleAssistantDelta(SID, delta('message_stop', SID, {}))

    store.addMessage(SID, {
      type: 'assistant',
      content: '',
      metadata: { message_id: MID, tool_uses: [{ id: 'toolu_1', name: 'Agent', input: {} }], has_tool_uses: true },
    })
    store.addMessage(SID, {
      type: 'assistant',
      content: '',
      metadata: { message_id: MID, tool_uses: [{ id: 'toolu_1', name: 'Agent', input: {} }], has_tool_uses: true },
    })

    const stored = store.messagesBySession.get(SID).find(m => m.message_id === MID)
    expect(stored.metadata.tool_uses).toHaveLength(1)
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
})
