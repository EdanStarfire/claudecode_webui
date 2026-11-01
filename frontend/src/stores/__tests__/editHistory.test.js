import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { createFakeHistoryServer } from './helpers/historyServer'

// Issue #2030 stage 4 PR 4b: editHistory refreshes from the message store's `onToolCompleted` event
// (live records only) instead of a watcher over every tool of every session.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-edit'
const sig = () => new AbortController().signal
const editCalls = () => apiMock.get.mock.calls.filter(([u]) => String(u).includes('/edit-history'))
const toolRecord = (id, name, status = 'completed', extra = {}) => ({
  type: 'tool_call', tool_use_id: id, name, status, input: { file_path: '/a' }, result: 'ok', ...extra,
})

async function setup() {
  setActivePinia(createPinia())
  apiMock.get.mockReset()
  apiMock.get.mockImplementation((url) => Promise.resolve(String(url).includes('/edit-history') ? { entries: [], tool_count: 0 } : {}))
  const { useMessageStore } = await import('@/stores/message')
  const { useEditHistoryStore } = await import('@/stores/editHistory')
  const store = useMessageStore()
  const editHistory = useEditHistoryStore()
  editHistory.entriesBySession.set(SID, { entries: [], tool_count: 0, loading: false, error: null })
  return { store, editHistory }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('live completions refresh the edit history once', () => {
  for (const name of ['Edit', 'Write', 'Bash']) {
    it(`${name}: a live completion triggers one debounced refresh`, async () => {
      const { store } = await setup()
      store.applyRecord(SID, toolRecord('t1', name, 'running'), 'live')
      await vi.advanceTimersByTimeAsync(1000)
      expect(editCalls()).toHaveLength(0)
      store.applyRecord(SID, toolRecord('t1', name), 'live')
      await vi.advanceTimersByTimeAsync(1000)
      expect(editCalls()).toHaveLength(1)
    })
  }

  it('a failed tool counts as completed (status error)', async () => {
    const { store } = await setup()
    store.applyRecord(SID, toolRecord('t1', 'Bash', 'failed', { result: undefined, error: 'boom' }), 'live')
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(1)
  })

  it('a tool created already completed is news', async () => {
    const { store } = await setup()
    store.applyRecord(SID, toolRecord('t1', 'Edit'), 'live')
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(1)
  })

  it('duplicate terminal updates of the same tool do not refresh again; other tools do (coalesced by the debounce)', async () => {
    const { store } = await setup()
    store.applyRecord(SID, toolRecord('t1', 'Edit'), 'live')
    store.applyRecord(SID, toolRecord('t1', 'Edit'), 'live')
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(1)
    store.applyRecord(SID, toolRecord('t2', 'Write'), 'live')
    store.applyRecord(SID, toolRecord('t3', 'Bash'), 'live')
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(2)
  })

  it('other tools, and a session whose history is not loaded, do not refresh', async () => {
    const { store, editHistory } = await setup()
    store.applyRecord(SID, toolRecord('r1', 'Read'), 'live')
    store.applyRecord(SID, toolRecord('g1', 'Grep'), 'live')
    editHistory.entriesBySession.delete(SID)
    store.applyRecord(SID, toolRecord('e1', 'Edit'), 'live')
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(0)
  })

  it('a tool that was denied (status completed) is news once', async () => {
    const { store } = await setup()
    store.applyRecord(SID, toolRecord('t1', 'Edit', 'denied', { result: undefined }), 'live')
    store.applyRecord(SID, toolRecord('t1', 'Edit', 'denied', { result: undefined }), 'live')
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(1)
  })
})

describe('history is not news', () => {
  it('records applied as load / archive / older page never refresh', async () => {
    const { store } = await setup()
    store.applyRecord(SID, toolRecord('l1', 'Edit'), 'load')
    store.applyRecord(SID, toolRecord('a1', 'Write'), 'archive')
    store.applyOlderPage(SID, [{ type: 'user', message_id: 'u0', content: 'x', timestamp: 1, metadata: {} }, toolRecord('o1', 'Bash')])
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(0)
  })

  it('a tool completed inside an island window does not refresh', async () => {
    const { store } = await setup()
    const records = []
    for (let t = 0; t < 40; t++) {
      records.push({ type: 'user', message_id: `u${t}`, timestamp: 10 + t * 3, content: `t${t}`, metadata: {} })
      records.push({ type: 'assistant', message_id: `a${t}`, timestamp: 11 + t * 3, content: 'x', metadata: { tool_uses: [{ id: `k${t}`, name: 'Edit' }] } })
      records.push(toolRecord(`k${t}`, 'Edit', 'completed', { message_id: `c${t}`, created_at: 12 + t * 3 }))
    }
    const server = createFakeHistoryServer(records, { tailStart: 100 })
    apiMock.get.mockImplementation((url) => (String(url).includes('/edit-history') ? Promise.resolve({ entries: [] }) : server.handle(url)))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    await vi.advanceTimersByTimeAsync(1000)
    await store.loadAround(SID, { messageId: 'u3' })
    expect(store.getIsland(SID)).toBeTruthy()
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(0)
  })
})

describe('resync reload', () => {
  it('re-fetching an already-loaded session refreshes the edit history once (completions that happened while the stream was down)', async () => {
    const { store } = await setup()
    const records = []
    for (let t = 0; t < 6; t++) {
      records.push({ type: 'user', message_id: `u${t}`, timestamp: 10 + t, content: `t${t}`, metadata: {} })
      records.push(toolRecord(`k${t}`, 'Edit', 'completed', { message_id: `c${t}`, created_at: 10 + t }))
    }
    const server = createFakeHistoryServer(records, { tailStart: 4 })
    apiMock.get.mockImplementation((url) => (String(url).includes('/edit-history') ? Promise.resolve({ entries: [] }) : server.handle(url)))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() }) // first load: nothing loaded yet
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(0)
    await store.loadMessages(SID, null, 0, { window: 'tail', resync: true, signal: sig() })
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(1)
  })

  it('an archive view never refreshes', async () => {
    const { store } = await setup()
    const server = createFakeHistoryServer([{ type: 'user', message_id: 'u0', timestamp: 1, content: 'x', metadata: {} }], { tailStart: 0 })
    apiMock.get.mockImplementation((url) => (String(url).includes('/edit-history') ? Promise.resolve({ entries: [] }) : server.handle(url)))
    for (let i = 0; i < 2; i++) {
      await store.loadMessages(SID, null, 0, { window: 'tail', source: 'archive', baseUrl: '/api/projects/p/archives/a/1/messages', signal: sig() })
    }
    await vi.advanceTimersByTimeAsync(1000)
    expect(editCalls()).toHaveLength(0)
  })
})

describe('onToolCompleted', () => {
  it('delivers (sessionId, toolUseId, name) for live completions only and unsubscribes', async () => {
    const { store } = await setup()
    const seen = []
    const off = store.onToolCompleted((...args) => seen.push(args))
    store.applyRecord(SID, toolRecord('t1', 'Edit', 'running'), 'live')
    store.applyRecord(SID, toolRecord('t1', 'Edit'), 'live')
    store.applyRecord(SID, toolRecord('t2', 'Edit'), 'load')
    expect(seen).toEqual([[SID, 't1', 'Edit']])
    off()
    store.applyRecord(SID, toolRecord('t3', 'Edit'), 'live')
    expect(seen).toHaveLength(1)
  })

  it('a throwing listener does not break the record path', async () => {
    const { store } = await setup()
    store.onToolCompleted(() => { throw new Error('boom') })
    expect(() => store.applyRecord(SID, toolRecord('t1', 'Edit'), 'live')).not.toThrow()
    expect(store.findLoadedToolCall(SID, 't1').status).toBe('completed')
  })
})
