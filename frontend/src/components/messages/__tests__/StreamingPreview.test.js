import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import { makeMessage } from '@/test-utils/factories'
import StreamingPreview from '@/components/messages/StreamingPreview.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const SESSION_ID = 'sess-1'

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

function delta(type, event) {
  return { uuid: 'env-' + Math.random(), event: { type, ...event } }
}

describe('StreamingPreview pending-tool indicator (Issue #1573)', () => {
  it('renders "Starting: <name>..." for a tool_use that starts mid-stream, before any card exists', async () => {
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    await nextTick()

    expect(screen.getByTestId('streaming-pending-tool')).toBeTruthy()
    expect(screen.getByText(/Starting: Bash/)).toBeTruthy()
  })

  it('hands off to the real card once the canonical assistant message lands (level-triggered, mirrors canonicalSeen)', async () => {
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    await nextTick()
    expect(screen.queryByTestId('streaming-pending-tool')).toBeTruthy()

    // The canonical assistant message for this turn lands — AssistantMessage.vue now has a
    // real segment/card for toolu_1, so the transient indicator must disappear.
    messageStore.addMessage(SESSION_ID, makeMessage({
      type: 'assistant', content: '', message_id: 'msg_1',
      metadata: { tool_uses: [{ id: 'toolu_1', name: 'Bash', input: { command: 'ls' } }] }
    }))
    await nextTick()

    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()
  })

  it('does not show a duplicate indicator for a content_block_start arriving after the canonical message already landed', async () => {
    // Out-of-order delivery across the two independent channels (assistant_delta vs message) —
    // the same class of race _clearStreamingPreviewContent's canonicalSeen already guards
    // against for text/thinking content.
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.addMessage(SESSION_ID, makeMessage({
      type: 'assistant', content: '', message_id: 'msg_1',
      metadata: { tool_uses: [{ id: 'toolu_1', name: 'Bash', input: { command: 'ls' } }] }
    }))
    await nextTick()

    // A stray content_block_start for the same turn arrives late.
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    await nextTick()

    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()
  })

  it('does not show the indicator for a content_block_start arriving after message_stop for this turn', async () => {
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('message_stop', {}))
    await nextTick()

    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    await nextTick()

    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()
  })

  it('interrupt discards the whole preview, including the pending-tool indicator', async () => {
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    await nextTick()
    expect(screen.queryByTestId('streaming-pending-tool')).toBeTruthy()

    messageStore.addMessage(SESSION_ID, makeMessage({ type: 'system', content: '', metadata: { subtype: 'interrupt' } }))
    await nextTick()

    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()
    expect(screen.queryByTestId('streaming-preview')).toBeNull()
  })

  it('does not duplicate the chip for a redelivered content_block_start of the same tool_use_id', async () => {
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    await nextTick()

    expect(screen.getAllByText(/Starting: Bash/).length).toBe(1)
  })

  it('stays visible when message_stop arrives before the canonical message, for a bare tool call with no text (review fix)', async () => {
    // Out-of-order-across-channels race: the stream channel's message_stop can arrive before
    // the canonical assistant message on the separate message channel — the exact race
    // #1955's canonicalSeen was built to survive for text/thinking. With no preceding text or
    // thinking, `preview.active` was the ONLY thing keeping the whole component mounted before
    // this fix; message_stop flips it false and would silently hide the still-pending indicator.
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 0,
      content_block: { type: 'tool_use', id: 'toolu_1', name: 'Bash' }
    }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('message_stop', {}))
    await nextTick()

    expect(screen.getByTestId('streaming-pending-tool')).toBeTruthy()
    expect(screen.getByText(/Starting: Bash/)).toBeTruthy()

    // The canonical message finally lands — now it's safe to hand off.
    messageStore.addMessage(SESSION_ID, makeMessage({
      type: 'assistant', content: '', message_id: 'msg_1',
      metadata: { tool_uses: [{ id: 'toolu_1', name: 'Bash', input: { command: 'ls' } }] }
    }))
    await nextTick()

    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()
  })

  it('keeps showing a second tool in a multi-canonical-message turn after the first tool is already claimed (review fix)', async () => {
    // addMessage()'s own #1955 comment documents that a single still-open turn can carry
    // multiple canonical assistant messages. canonicalSeen flips true after the first one and
    // stays true for the rest of the turn, so gating registration on it would silently drop the
    // second tool's indicator. Gating per-id on claimedToolIds instead must not have that gap.
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 0,
      content_block: { type: 'tool_use', id: 'toolu_a', name: 'Read' }
    }))

    // First canonical message of this still-open turn claims tool A only.
    messageStore.addMessage(SESSION_ID, makeMessage({
      type: 'assistant', content: 'Checking the file first.', message_id: 'msg_1-a',
      metadata: { tool_uses: [{ id: 'toolu_a', name: 'Read', input: { file_path: '/tmp/a' } }] }
    }))
    await nextTick()
    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()

    // A second tool_use starts streaming later in the SAME still-open turn, after that first
    // canonical message already landed.
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_b', name: 'Bash' }
    }))
    await nextTick()

    expect(screen.getByText(/Starting: Bash/)).toBeTruthy()

    // Its own canonical message lands — only B should be claimed/cleared, not reopen A.
    messageStore.addMessage(SESSION_ID, makeMessage({
      type: 'assistant', content: '', message_id: 'msg_1-b',
      metadata: { tool_uses: [{ id: 'toolu_b', name: 'Bash', input: { command: 'ls' } }] }
    }))
    await nextTick()

    expect(screen.queryByTestId('streaming-pending-tool')).toBeNull()
  })

  it('shows one chip per tool for multiple tool calls in the same turn', async () => {
    const { pinia } = renderWithStores(StreamingPreview, { props: { sessionId: SESSION_ID } })
    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)

    messageStore.handleAssistantDelta(SESSION_ID, delta('message_start', { message: { id: 'msg_1' } }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 1,
      content_block: { type: 'tool_use', id: 'toolu_a', name: 'Read' }
    }))
    messageStore.handleAssistantDelta(SESSION_ID, delta('content_block_start', {
      index: 2,
      content_block: { type: 'tool_use', id: 'toolu_b', name: 'Bash' }
    }))
    await nextTick()

    expect(screen.getByText(/Starting: Read/)).toBeTruthy()
    expect(screen.getByText(/Starting: Bash/)).toBeTruthy()
  })
})
