import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia } from 'pinia'
import { nextTick, h, defineComponent } from 'vue'
import { renderWithStores } from '@/test-utils/render'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { createFakeHistoryServer } from '@/stores/__tests__/helpers/historyServer'
import AssistantMessage from '@/components/messages/AssistantMessage.vue'

// Issue #2326: segEnrichedToolCalls resolves each tool_use through the store's O(1) indexed
// lookup; it must hand ActivityTimeline the same live reactive entries the linear find did.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))
vi.mock('@/composables/useMarkdown', () => ({ useMarkdownPlugins: () => [], useMarkdownComponents: () => ({}) }))

const SID = 'sess-2326'
let seen
const TimelineStub = defineComponent({
  props: ['tools'],
  setup(props) {
    return () => { seen = props.tools; return h('div', { 'data-testid': 'tl' }, props.tools.map(t => `${t.id}:${t.status}`).join(',')) }
  },
})

function mountRow(toolIds) {
  const message = {
    id: 'm1', message_id: 'm1', type: 'assistant', content: 'hi', timestamp: 1000,
    metadata: { tool_uses: toolIds.map(id => ({ id, name: 'Bash', input: { command: id } })) },
  }
  const r = renderWithStores(AssistantMessage, {
    props: { message, attachedTools: [] },
    stubs: { ActivityTimeline: TimelineStub },
  })
  setActivePinia(r.pinia)
  const session = useSessionStore()
  session.currentSessionId = SID
  return r
}

function seedSuffix(store, calls) {
  const arr = calls.map(c => ({ name: 'Bash', input: {}, result: null, ...c }))
  const idx = new Map(arr.map((c, i) => [c.id, i]))
  store.toolCallsBySession.set(SID, arr)
  store.toolIndexBySession.set(SID, idx)
}

beforeEach(() => { seen = undefined })

describe('AssistantMessage tool lookup (issue #2326)', () => {
  it('returns the live store entries (identity), pending fallback for an unknown id', async () => {
    const r = mountRow(['a', 'zzz'])
    const store = useMessageStore()
    seedSuffix(store, [{ id: 'a', status: 'completed' }, { id: 'other', status: 'completed' }])
    store.toolCallsBySession.set(SID, store.toolCallsBySession.get(SID))
    r.wrapper.vm.$forceUpdate()
    await nextTick()
    expect(seen[0]).toBe(store.findLoadedToolCall(SID, 'a'))
    expect(seen[0].status).toBe('completed')
    expect(seen[1]).toMatchObject({ id: 'zzz', status: 'pending', isExpanded: true })
  })

  it('re-renders when the live entry is mutated', async () => {
    const r = mountRow(['a'])
    const store = useMessageStore()
    seedSuffix(store, [{ id: 'a', status: 'executing' }])
    r.wrapper.vm.$forceUpdate()
    await nextTick()
    const live = store.findLoadedToolCall(SID, 'a')
    live.status = 'completed'
    await nextTick()
    expect(r.container.textContent).toContain('a:completed')
  })

  it('swaps pending for the live entry when the call arrives after the row mounted', async () => {
    const r = mountRow(['late'])
    const store = useMessageStore()
    await nextTick()
    expect(r.container.textContent).toContain('late:pending')
    seedSuffix(store, [{ id: 'late', status: 'completed' }])
    await nextTick()
    expect(r.container.textContent).toContain('late:completed')
  })

  it('resolves island and suffix calls through a real island (suffix wins on a shared id)', async () => {
    const records = []
    for (let t = 0; t < 60; t++) {
      records.push({ type: 'user', message_id: `u${t}`, timestamp: 1000 + t * 10, content: `turn ${t}`, metadata: {} })
      records.push({
        type: 'assistant', message_id: `a${t}`, timestamp: 1001 + t * 10, content: `answer ${t}`,
        metadata: { turn_id: `turn-${t}`, has_tool_uses: true, tool_uses: [{ id: `tool${t}`, name: 'Bash', input: {} }] },
      })
      records.push({
        type: 'tool_call', message_id: `tc${t}`, tool_use_id: `tool${t}`, name: 'Bash', status: 'completed',
        input: {}, result: `out ${t}`, turn_id: `turn-${t}`, created_at: 1002 + t * 10,
      })
    }
    const server = createFakeHistoryServer(records, { tailStart: records.length - 12 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    const r = mountRow(['tool20', 'tool59'])
    const store = useMessageStore()
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: new AbortController().signal })
    await store.loadAround(SID, { cursor: 'b:60' })
    expect(store.getIsland(SID)).toBeTruthy()
    r.wrapper.vm.$forceUpdate()
    await nextTick()
    expect(seen[0]).toBe(store.findLoadedToolCall(SID, 'tool20'))
    expect(seen[0].status).toBe('completed')
    expect(seen[1]).toBe(store.findLoadedToolCall(SID, 'tool59'))
    expect(store.loadedToolCalls(SID).filter(t => t.id === 'tool59')).toHaveLength(1)
  })
})
