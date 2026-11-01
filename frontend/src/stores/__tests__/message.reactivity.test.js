import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { effectScope, computed, watch, nextTick, isReactive, isProxy, isShallow } from 'vue'
import { createFakeHistoryServer } from './helpers/historyServer'

// Issue #2030 stage 4 PR 4b: the loaded history is not deeply reactive (shallowRef Maps, markRaw
// messages, shallowReactive tool entries). This is the writer-trigger matrix: for EVERY writer of a
// message/tool field, a dependent computed reading through each public read path must re-run exactly
// once and show the new value. A writer that mutates without notifying fails here.

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-react'
const sig = () => new AbortController().signal

async function freshStore() {
  setActivePinia(createPinia())
  apiMock.get.mockReset()
  const { useMessageStore } = await import('@/stores/message')
  return useMessageStore()
}

/** A lazily evaluated dependent: `runs()` counts evaluations, `read()` re-reads it (fresh or cached). */
function probe(fn) {
  const scope = effectScope()
  let runs = 0
  const c = scope.run(() => computed(() => { runs++; return fn() }))
  return { runs: () => runs, read: () => c.value, stop: () => scope.stop() }
}

/** Applies `mutate`, then asserts the probe re-ran exactly once and now equals `expected`. */
function expectRerunOnce(p, mutate, expected, label) {
  p.read()
  const before = p.runs()
  mutate()
  const value = p.read()
  expect(p.runs() - before, `${label}: re-runs`).toBe(1)
  expect(value, `${label}: value`).toEqual(expected)
}

const READ_PATHS = {
  loadedToolCalls: (store, id, field) => () => store.loadedToolCalls(SID).find(t => t.id === id)?.[field],
  findLoadedToolCall: (store, id, field) => () => store.findLoadedToolCall(SID, id)?.[field],
}

function live(store, record) { store.applyRecord(SID, record, 'live') }

function tool(id, extra = {}) {
  return { type: 'tool_call', tool_use_id: id, name: 'Bash', status: 'running', input: { command: 'ls' }, ...extra }
}

describe('tool-entry writers re-trigger every read path (suffix)', () => {
  // [field, update record fragment, expected value (or predicate)]
  const UPDATES = [
    ['status', { status: 'completed' }, 'completed'],
    ['input', { input: { command: 'pwd' } }, { command: 'pwd' }],
    ['timestamp', { created_at: '2031-01-01T00:00:00Z', turn_id: 't9' }, '2031-01-01T00:00:00Z'],
    ['messageId', { turn_id: 't9' }, 't9'],
    ['answers', { updated_input: { answers: { q: 'a' } } }, { q: 'a' }],
    ['suggestions', { permission: { suggestions: [{ type: 'addRules' }] } }, [{ type: 'addRules' }]],
    ['permissionRequestId', { permission: { suggestions: [] }, request_id: 'rq1' }, 'rq1'],
    ['permissionDecision', { permission_granted: true }, 'allow'],
    ['appliedUpdates', { applied_updates: [{ a: 1 }] }, [{ a: 1 }]],
    ['result', { result: 'out' }, { error: false, content: 'out' }],
    ['isExpanded', { status: 'completed' }, false],
    ['autoApprovedReason', { auto_approved_reason: 'rule' }, 'rule'],
    ['senderAttachments', { sender_attachments: ['res1'] }, ['res1']],
  ]

  // The store getters above also re-run on the Map replacement every writer performs, so they cannot
  // prove the FIELD write itself is reactive. This path reads a captured entry directly: it only
  // re-runs if the shallowReactive entry notifies.
  describe('captured entry (isolates the field write from the Map replacement)', () => {
    for (const [field, fragment, expected] of UPDATES) {
      it(`_applyToolCallRecord update: ${field}`, async () => {
        const store = await freshStore()
        live(store, tool('t1'))
        const entry = store.findLoadedToolCall(SID, 't1')
        const p = probe(() => entry[field])
        expectRerunOnce(p, () => live(store, tool('t1', fragment)), expected, field)
        p.stop()
      })
    }

    it('updateToolCall (handlePermissionResponse) and toggleToolExpansion write reactive fields', async () => {
      const store = await freshStore()
      live(store, tool('t1', { status: 'awaiting_permission', request_id: 'rq1', permission: { suggestions: [] } }))
      const entry = store.findLoadedToolCall(SID, 't1')
      const decision = probe(() => entry.permissionDecision)
      expectRerunOnce(decision, () => store.handlePermissionResponse(SID, { request_id: 'rq1', decision: 'allow' }), 'allow', 'allow')
      const expanded = probe(() => entry.isExpanded)
      const start = expanded.read()
      expectRerunOnce(expanded, () => store.toggleToolExpansion(SID, 't1'), !start, 'toggle')
    })

    it('terminal resolution stamps are reactive on the captured entry', async () => {
      const store = await freshStore()
      live(store, tool('t1'))
      const entry = store.findLoadedToolCall(SID, 't1')
      const orphaned = probe(() => entry._isOrphaned)
      expectRerunOnce(orphaned, () => live(store, tool('t1', { status: 'interrupted' })), true, 'interrupted')
    })
  })

  for (const [pathName, makeReader] of Object.entries(READ_PATHS)) {
    describe(pathName, () => {
      for (const [field, fragment, expected] of UPDATES) {
        it(`_applyToolCallRecord update: ${field}`, async () => {
          const store = await freshStore()
          live(store, tool('t1'))
          const p = probe(makeReader(store, 't1', field))
          expectRerunOnce(p, () => live(store, tool('t1', fragment)), expected, field)
          p.stop()
        })
      }

      it('_applyTerminalResolution: denied sets result and collapses', async () => {
        const store = await freshStore()
        live(store, tool('t1'))
        const result = probe(makeReader(store, 't1', 'result'))
        expectRerunOnce(result, () => live(store, tool('t1', { status: 'denied' })), { error: true, message: 'Permission denied' }, 'denied result')
        const expanded = probe(makeReader(store, 't1', 'isExpanded'))
        expect(expanded.read()).toBe(false)
      })

      it('_applyTerminalResolution: interrupted stamps the orphaned fields', async () => {
        const store = await freshStore()
        live(store, tool('t1'))
        const orphaned = probe(makeReader(store, 't1', '_isOrphaned'))
        const info = probe(makeReader(store, 't1', '_orphanedInfo'))
        orphaned.read(); info.read()
        live(store, tool('t1', { status: 'interrupted' }))
        expect(orphaned.read()).toBe(true)
        expect(info.read()).toEqual({ reason: 'denied', message: 'Session was interrupted' })
      })

      it('create: a new tool appears in the dependent', async () => {
        const store = await freshStore()
        live(store, tool('t1'))
        const p = probe(() => store.loadedToolCalls(SID).map(t => t.id).join(','))
        expectRerunOnce(p, () => live(store, tool('t2')), 't1,t2', 'create')
        expect(makeReader(store, 't2', 'status')()).toBe('executing')
      })

      it('handlePermissionResponse allow / deny (updateToolCall)', async () => {
        const store = await freshStore()
        live(store, tool('t1', { status: 'awaiting_permission', request_id: 'rq1', permission: { suggestions: [] } }))
        live(store, tool('t2', { status: 'awaiting_permission', request_id: 'rq2', permission: { suggestions: [] } }))
        const decision = probe(makeReader(store, 't1', 'permissionDecision'))
        expectRerunOnce(decision, () => store.handlePermissionResponse(SID, { request_id: 'rq1', decision: 'allow' }), 'allow', 'allow')
        expect(probe(makeReader(store, 't1', 'status')).read()).toBe('executing')
        const denied = probe(makeReader(store, 't2', 'result'))
        expectRerunOnce(denied, () => store.handlePermissionResponse(SID, { request_id: 'rq2', decision: 'deny', reasoning: 'no' }), { error: true, message: 'no' }, 'deny')
        expect(probe(makeReader(store, 't2', 'status')).read()).toBe('completed')
      })

      it('toggleToolExpansion on a suffix tool', async () => {
        const store = await freshStore()
        live(store, tool('t1'))
        const p = probe(makeReader(store, 't1', 'isExpanded'))
        expectRerunOnce(p, () => store.toggleToolExpansion(SID, 't1'), false, 'toggle')
        expectRerunOnce(p, () => store.toggleToolExpansion(SID, 't1'), true, 'toggle back')
      })
    })
  }

  it('a direct field read through the entry (no store getter) re-runs without a Map replacement', async () => {
    const store = await freshStore()
    live(store, tool('t1'))
    const entry = store.findLoadedToolCall(SID, 't1')
    const p = probe(() => entry.status)
    expectRerunOnce(p, () => { entry.status = 'completed' }, 'completed', 'entry write')
  })
})

describe('message writers re-trigger dependents', () => {
  it('an appended live message', async () => {
    const store = await freshStore()
    live(store, { type: 'user', message_id: 'm1', content: 'a', timestamp: 1, metadata: {} })
    const p = probe(() => { const m = store.loadedMessages(SID); return `${m.length}:${m.at(-1)?.content}` })
    expectRerunOnce(p, () => live(store, { type: 'user', message_id: 'm2', content: 'b', timestamp: 2, metadata: {} }), '2:b', 'append')
  })

  it('the api_retry in-place replacement', async () => {
    const store = await freshStore()
    const retry = (attempt, id) => ({
      type: 'system', message_id: id, content: `attempt ${attempt}`, timestamp: attempt,
      metadata: { subtype: 'api_retry', retry_message_id: 'retry-1', attempt },
    })
    live(store, retry(1, 'r1'))
    const p = probe(() => store.loadedMessages(SID).map(m => m.content).join('|'))
    expectRerunOnce(p, () => live(store, retry(2, 'r2')), 'attempt 2', 'api_retry replace')
    expect(isReactive(store.messagesBySession.get(SID)[0])).toBe(false)
  })

  it('clearMessages and clearArchiveMessages', async () => {
    const store = await freshStore()
    live(store, { type: 'user', message_id: 'm1', content: 'a', timestamp: 1, metadata: {} })
    live(store, tool('t1'))
    const messages = probe(() => store.loadedMessages(SID).length)
    const tools = probe(() => store.loadedToolCalls(SID).length)
    expectRerunOnce(messages, () => store.clearMessages(SID), 0, 'clearMessages messages')
    expect(tools.read()).toBe(0)
    store.setArchiveMessages(SID, [{ type: 'user', message_id: 'a1', content: 'x', timestamp: 1, metadata: {} }, tool('ta')])
    expect(messages.read()).toBe(1)
    expect(tools.read()).toBe(1)
    expectRerunOnce(messages, () => store.clearArchiveMessages(SID), 0, 'clearArchiveMessages messages')
    expect(tools.read()).toBe(0)
    const has = probe(() => store.messagesBySession.has(SID))
    expect(has.read()).toBe(false)
  })

  it('setArchiveMessages installs and notifies', async () => {
    const store = await freshStore()
    const p = probe(() => store.loadedMessages(SID).length)
    expectRerunOnce(p, () => store.setArchiveMessages(SID, [
      { type: 'user', message_id: 'a1', content: 'x', timestamp: 1, metadata: {} },
      { type: 'user', message_id: 'a2', content: 'y', timestamp: 2, metadata: {} },
    ]), 2, 'setArchiveMessages')
  })

  it('loadMessages (legacy paged path) installs and notifies', async () => {
    const store = await freshStore()
    const p = probe(() => `${store.loadedMessages(SID).length}/${store.loadedToolCalls(SID).length}`)
    p.read()
    apiMock.get.mockResolvedValue({
      messages: [
        { type: 'user', message_id: 'm1', content: 'a', timestamp: 1, metadata: {} },
        tool('t1', { message_id: 'tc1' }),
      ],
      total_count: 2, has_more: false, event_cursor: 1,
    })
    const before = p.runs()
    await store.loadMessages(SID)
    expect(p.read()).toBe('1/1')
    expect(p.runs() - before).toBeGreaterThanOrEqual(1)
  })
})

describe('history windows (tail load, older pages, island) re-trigger dependents', () => {
  // X: first record (running, with input) in turn 5, final record (completed, no input) in the last turn.
  function buildRecords(turns = 40) {
    const records = []
    for (let t = 0; t < turns; t++) {
      records.push({ type: 'user', message_id: `u${t}`, timestamp: 1000 + t * 10, content: `turn ${t}`, metadata: {} })
      records.push({
        type: 'assistant', message_id: `a${t}`, timestamp: 1001 + t * 10, content: `answer ${t}`,
        metadata: { turn_id: `turn-${t}`, has_tool_uses: true, tool_uses: [{ id: `tool${t}`, name: 'Bash', input: { command: `echo ${t}` } }] },
      })
      records.push({
        type: 'tool_call', message_id: `tc${t}`, tool_use_id: `tool${t}`, name: 'Bash', status: 'completed',
        input: { command: `echo ${t}` }, result: `out ${t}`, turn_id: `turn-${t}`, created_at: 1002 + t * 10,
      })
      if (t === 5) {
        records.push({ type: 'tool_call', message_id: 'x1', tool_use_id: 'X', name: 'Bash', status: 'running', input: { command: 'ls' }, parent_tool_use_id: 'P', turn_id: 'turn-5', created_at: 1003 + t * 10 })
      }
      if (t === turns - 1) {
        records.push({ type: 'tool_call', message_id: 'x2', tool_use_id: 'X', name: 'Bash', status: 'completed', turn_id: `turn-${t}`, created_at: 1004 + t * 10 })
      }
    }
    return records
  }

  async function windowed(options = {}) {
    const store = await freshStore()
    const records = buildRecords()
    const server = createFakeHistoryServer(records, { tailStart: records.length - 10, pageSize: 20, ...options })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    return { store, server }
  }

  it('tail load installs and notifies', async () => {
    const store = await freshStore()
    const records = buildRecords()
    const server = createFakeHistoryServer(records, { tailStart: records.length - 10 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    const p = probe(() => `${store.loadedMessages(SID).length}/${store.loadedToolCalls(SID).length}`)
    expect(p.read()).toBe('0/0')
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    expect(p.read()).not.toBe('0/0')
  })

  it('applyOlderPage prepends and mergeToolCallFields fills the CURRENT entry (each page re-runs the dependents once)', async () => {
    const { store } = await windowed()
    const messages = probe(() => store.loadedMessages(SID).length)
    const input = probe(READ_PATHS.findLoadedToolCall(store, 'X', 'input'))
    const parent = probe(READ_PATHS.loadedToolCalls(store, 'X', 'parent_tool_use_id'))
    expect(input.read()).toBeUndefined()
    messages.read()
    let filledOnPage = null
    for (let page = 0; page < 20 && store.getHistoryState(SID)?.hasMoreBefore !== false; page++) {
      const fetched = await store.fetchOlderPage(SID, { signal: sig() })
      if (!fetched) break
      const [m0, i0, p0] = [messages.runs(), input.runs(), parent.runs()]
      const mBefore = messages.read()
      expect(store.applyFetchedOlderPage(SID, fetched)).toBe(true)
      expect(messages.read()).toBeGreaterThan(mBefore)
      expect(messages.runs() - m0).toBe(1)
      if (filledOnPage === null && input.read() !== undefined) {
        filledOnPage = page
        expect(input.runs() - i0).toBe(1)
        expect(parent.read()).toBe('P')
        expect(parent.runs() - p0).toBe(1)
      }
    }
    expect(filledOnPage).not.toBeNull()
    expect(input.read()).toEqual({ command: 'ls' })
  })

  it('island: loadAround installs, toggleToolExpansion on an island tool, then _shareIslandTools fills the suffix entry', async () => {
    const { store } = await windowed()
    const loaded = probe(() => store.loadedMessages(SID).length)
    const input = probe(READ_PATHS.findLoadedToolCall(store, 'X', 'input'))
    expect(input.read()).toBeUndefined()
    const before = loaded.read()
    const result = await store.loadAround(SID, { messageId: 'x1' })
    expect(result.status).toBe('island')
    expect(loaded.read()).toBeGreaterThan(before)
    // X lives in the suffix, so the island's copy was dropped and merged into the suffix entry
    expect(input.read()).toEqual({ command: 'ls' })
    expect(store.getIsland(SID).toolCalls.some(t => t.id === 'X')).toBe(false)

    // an island-owned tool: toggle through both read paths
    const owned = 'tool5'
    expect(store.getIsland(SID).toolCalls.some(t => t.id === owned)).toBe(true)
    for (const make of Object.values(READ_PATHS)) {
      const p = probe(make(store, owned, 'isExpanded'))
      const start = p.read()
      expectRerunOnce(p, () => store.toggleToolExpansion(SID, owned), !start, 'island toggle')
    }
    expect(store.loadedToolCalls(SID).find(t => t.id === owned)).toBe(store.findLoadedToolCall(SID, owned))
  })

  it('island growth (after pages) notifies, and the page that touches the suffix merges the island into it', async () => {
    const { store } = await windowed({ afterPageSize: 8 })
    await store.loadAround(SID, { messageId: 'u20' })
    const count = probe(() => store.loadedMessages(SID).length)
    const tools = probe(() => store.loadedToolCalls(SID).length)
    const island = probe(() => !!store.getIsland(SID))
    count.read(); tools.read()
    expect(island.read()).toBe(true)
    let merged = false
    for (let i = 0; i < 40 && !merged; i++) {
      const page = await store.fetchIslandPage(SID, 'after', { signal: sig() })
      if (!page) break
      const [c0, t0] = [count.runs(), tools.runs()]
      const before = count.read()
      expect(store.applyIslandPage(SID, page)).toBe(true)
      expect(count.read()).toBeGreaterThan(before)
      expect(count.runs() - c0).toBe(1)
      tools.read()
      expect(tools.runs() - t0).toBeLessThanOrEqual(1)
      merged = !store.getIsland(SID)
    }
    expect(merged).toBe(true)
    expect(island.read()).toBe(false)
    expect(count.read()).toBe(store.messagesBySession.get(SID).length)
    expect(tools.read()).toBe(store.toolCallsBySession.get(SID).length)
  })
})

describe('loadedToolCalls island union cache', () => {
  it('returns the same union until an input changes, then rebuilds; drops with the island', async () => {
    const store = await freshStore()
    const records = []
    for (let t = 0; t < 40; t++) {
      records.push({ type: 'user', message_id: `u${t}`, timestamp: 10 + t * 3, content: `t${t}`, metadata: {} })
      records.push({ type: 'assistant', message_id: `a${t}`, timestamp: 11 + t * 3, content: 'x', metadata: { tool_uses: [{ id: `k${t}`, name: 'Bash' }] } })
      records.push({ type: 'tool_call', message_id: `c${t}`, tool_use_id: `k${t}`, name: 'Bash', status: 'completed', input: {}, result: 'r', created_at: 12 + t * 3 })
    }
    const server = createFakeHistoryServer(records, { tailStart: 90 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    await store.loadAround(SID, { messageId: 'u3' })
    const first = store.loadedToolCalls(SID)
    expect(store.loadedToolCalls(SID)).toBe(first) // cache hit: same identity
    store.toggleToolExpansion(SID, 'k35') // field write / Map replacement alone does not change the union
    expect(store.loadedToolCalls(SID)).toBe(first)
    live(store, tool('fresh'))
    const second = store.loadedToolCalls(SID)
    expect(second).not.toBe(first) // suffix append invalidates
    expect(second.map(t => t.id)).toEqual([...first.map(t => t.id), 'fresh'])
    const ids = second.map(t => t.id)
    expect(new Set(ids).size).toBe(ids.length)
    const page = await store.fetchIslandPage(SID, 'before', { signal: sig() })
    if (page) {
      store.applyIslandPage(SID, page)
      expect(store.loadedToolCalls(SID)).not.toBe(second) // island growth invalidates
    }
    store.clearMessages(SID)
    expect(store.loadedToolCalls(SID)).toEqual([])
  })
})

describe('the loaded-list getters track the Maps (a watcher on a same-identity computed would not fire)', () => {
  it('watch on the getter fires per append; watch on a wrapping computed does not', async () => {
    const store = await freshStore()
    live(store, { type: 'user', message_id: 'm1', content: 'a', timestamp: 1, metadata: {} })
    const wrapped = computed(() => store.loadedMessages(SID))
    const viaGetter = vi.fn()
    const viaWrapper = vi.fn()
    watch(() => store.loadedMessages(SID).length, viaGetter)
    watch(() => wrapped.value.length, viaWrapper)
    live(store, { type: 'user', message_id: 'm2', content: 'b', timestamp: 2, metadata: {} })
    await nextTick()
    expect(viaGetter).toHaveBeenCalledTimes(1)
    // documents WHY MessageList reads the getter inside its watchers: the wrapper returns the
    // same raw array, so its dependents are never notified.
    expect(viaWrapper).toHaveBeenCalledTimes(0)
  })
})

describe('no per-record reactivity is created for loaded history', () => {
  it('messages are raw, tool entries are shallowReactive, the containers are not reactive', async () => {
    const store = await freshStore()
    live(store, { type: 'user', message_id: 'm1', content: 'a', timestamp: 1, metadata: {} })
    live(store, tool('t1'))
    live(store, { type: 'system', message_id: 'r1', content: 'x', timestamp: 1, metadata: { subtype: 'api_retry', retry_message_id: 'rr' } })
    live(store, { type: 'system', message_id: 'r2', content: 'y', timestamp: 2, metadata: { subtype: 'api_retry', retry_message_id: 'rr' } })
    const messages = store.messagesBySession.get(SID)
    const tools = store.toolCallsBySession.get(SID)
    expect(isReactive(messages)).toBe(false)
    expect(isReactive(tools)).toBe(false)
    for (const m of messages) {
      expect(isProxy(m)).toBe(false)
      expect(isReactive(m)).toBe(false)
    }
    for (const t of tools) {
      expect(isProxy(t)).toBe(true)
      expect(isShallow(t)).toBe(true)
      expect(isReactive(t.input)).toBe(false)
    }
    // the Maps themselves (pinia unwraps the shallowRef): plain, not reactive
    expect(isReactive(store.messagesBySession)).toBe(false)
    expect(isReactive(store.toolCallsBySession)).toBe(false)
  })

  it('history entries (older page + island) are created the same way', async () => {
    const store = await freshStore()
    const records = []
    for (let t = 0; t < 30; t++) {
      records.push({ type: 'user', message_id: `u${t}`, timestamp: 10 + t * 3, content: `t${t}`, metadata: {} })
      records.push({ type: 'assistant', message_id: `a${t}`, timestamp: 11 + t * 3, content: 'x', metadata: { tool_uses: [{ id: `k${t}`, name: 'Bash' }] } })
      records.push({ type: 'tool_call', message_id: `c${t}`, tool_use_id: `k${t}`, name: 'Bash', status: 'completed', input: {}, result: 'r', created_at: 12 + t * 3 })
    }
    const server = createFakeHistoryServer(records, { tailStart: 75, pageSize: 6 })
    apiMock.get.mockImplementation((url) => server.handle(url))
    await store.loadMessages(SID, null, 0, { window: 'tail', signal: sig() })
    store.applyFetchedOlderPage(SID, await store.fetchOlderPage(SID, { signal: sig() }))
    await store.loadAround(SID, { messageId: 'u3' })
    for (const t of store.loadedToolCalls(SID)) {
      expect(isProxy(t) && isShallow(t)).toBe(true)
    }
    for (const m of store.loadedMessages(SID)) expect(isProxy(m)).toBe(false)
  })
})
