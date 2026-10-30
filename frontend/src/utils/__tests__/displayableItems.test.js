import { describe, it, expect } from 'vitest'
import { buildDisplayableItems, buildIntervalItems, getItemKey, computeItemKeys } from '../displayableItems'

const SID = 's1'
const ts = (sec) => 1_700_000_000 + sec

function user(id, sec, content = 'hi') {
  return { type: 'user', message_id: id, content, timestamp: ts(sec), metadata: {} }
}
function assistant(id, sec, content = 'ok') {
  return { type: 'assistant', message_id: id, content, timestamp: ts(sec), metadata: { turn_id: `turn-${id}` } }
}

function build(messages, extra = {}) {
  return buildDisplayableItems({
    sessionId: SID, messages, toolCalls: [], legEntries: [],
    getTaskIdForLaunchToolUse: () => null, isHookMessageAttached: () => false,
    ...extra,
  })
}

describe('getItemKey (issue #2030)', () => {
  it('keys rows by stable record identity, independent of index', () => {
    const messages = [user('u1', 0), assistant('a1', 1), user('u2', 2), assistant('a2', 3)]
    const full = build(messages)
    const tail = build(messages.slice(2))
    const keyOf = (items, id) => getItemKey(items.find(i => i.message?.message_id === id))
    expect(keyOf(tail, 'u2')).toBe(keyOf(full, 'u2'))
    expect(keyOf(tail, 'a2')).toBe(keyOf(full, 'a2'))
    expect(keyOf(full, 'u1')).not.toBe(keyOf(full, 'a1'))
  })

  it('keeps every key unique and stable across a prepend', () => {
    const messages = Array.from({ length: 30 }, (_, i) => (i % 2 ? assistant(`a${i}`, i) : user(`u${i}`, i)))
    const tail = build(messages.slice(10))
    const full = build(messages)
    const tailKeys = tail.map((item, i) => getItemKey(item, i))
    const fullKeys = full.map((item, i) => getItemKey(item, i))
    expect(new Set(fullKeys).size).toBe(fullKeys.length)
    // Every row of the tail is still present, with the same key, in the full list.
    for (const key of tailKeys.filter(k => !k.startsWith('date:'))) expect(fullKeys).toContain(key)
  })

  it('keys date separators by day and signals by their key', () => {
    expect(getItemKey({ type: 'date_separator', dateKey: 'Tue Oct 27 2026' })).toBe('date:Tue Oct 27 2026')
    expect(getItemKey({ type: 'subagent_signal', key: 'k1' })).toBe('signal:k1')
    expect(getItemKey({ type: 'compaction', messages: [{ message_id: 'c1' }] })).toBe('c:c1')
    expect(getItemKey({ type: 'message', message: {} }, 4)).toBe('i:4')
  })

  it('stamps the date separator with its day key', () => {
    const items = build([user('u1', 0)])
    const separator = items.find(i => i.type === 'date_separator')
    expect(getItemKey(separator)).toBe(`date:${separator.dateKey}`)
  })
})

describe('compaction group key (issue #2030 stage 2)', () => {
  const sys = (id, sec, metadata) => ({ type: 'system', message_id: id, content: '', timestamp: ts(sec), metadata })
  const group = (prefix, sec) => [
    sys(`${prefix}-compacting`, sec, { subtype: 'status', init_data: { status: 'compacting' } }),
    sys(`${prefix}-idle`, sec + 1, { subtype: 'status', init_data: { status: null } }),
    sys(`${prefix}-boundary`, sec + 2, { subtype: 'compact_boundary' }),
    user(`${prefix}-cont`, sec + 3, 'This session is being continued from a previous conversation that ran out of context.'),
  ]

  it('keys each group by its first record message_id, independent of preceding groups', () => {
    const messages = [user('u1', 0), ...group('g1', 10), user('u2', 20), ...group('g2', 30), user('u3', 40)]
    const groups = build(messages).filter(i => i.type === 'compaction')
    expect(groups.map(g => g.groupKey)).toEqual(['g1-compacting', 'g2-compacting'])
    // a window starting after the first group sees the same key for the second one
    const tail = build(messages.slice(5)).filter(i => i.type === 'compaction')
    expect(tail.map(g => g.groupKey)).toEqual(['g2-compacting'])
    expect(tail.every(g => g.groupIndex === undefined)).toBe(true) // the ordinal path is gone
  })
})

describe('leg-terminal signals across the window boundary (issue #2030, plan §5 exception 1)', () => {
  const leg = (taskId, endedAt) => ({
    task_id: taskId, session_id: SID,
    legs: [{ status: 'completed', ended_at: endedAt, description: taskId, result: 'done', tool_use_id: `tu-${taskId}` }],
  })

  it('drops terminal signals that precede the first loaded item while the interval has older history not loaded', () => {
    const messages = [user('u1', 100), assistant('a1', 101)]
    const legEntries = [leg('old-1', ts(1)), leg('old-2', ts(2)), leg('old-3', ts(3)), leg('inside', ts(100.5))]
    const partial = build(messages, { legEntries, openBefore: true })
    const keys = partial.filter(i => i.type === 'subagent_signal').map(i => i.taskId)
    expect(keys).toEqual(['inside'])
    // Once the history is complete the pre-window signals are placed normally again.
    const complete = build(messages, { legEntries, openBefore: false })
    expect(complete.filter(i => i.type === 'subagent_signal').map(i => i.taskId).sort())
      .toEqual(['inside', 'old-1', 'old-2', 'old-3'])
  })

  it('keeps every signal when history is complete (the default)', () => {
    const messages = [user('u1', 100)]
    const items = build(messages, { legEntries: [leg('old', ts(1))] })
    expect(items.filter(i => i.type === 'subagent_signal')).toHaveLength(1)
  })
})

describe('computeItemKeys (issue #2030)', () => {
  it('keeps keys unique when a date separator for the same day recurs (timestamps running backwards)', () => {
    // today, 1970 (e.g. a replayed fixture record), today again: three separators, two for "today".
    const now = 1_790_000_000
    const items = build([user('u1', 0), { ...assistant('a1', 1), timestamp: 1_000_006 }, user('u2', 2)].map((m, i) => (
      i === 1 ? m : { ...m, timestamp: now + i }
    )))
    const separators = items.filter(i => i.type === 'date_separator')
    expect(separators.length).toBeGreaterThanOrEqual(3)
    const keys = computeItemKeys(items)
    expect(new Set(keys).size).toBe(keys.length)
    // The first occurrence keeps the plain stable key; only later duplicates are suffixed.
    expect(keys[0]).toBe(getItemKey(items[0], 0))
  })
})

// Issue #2030 (stage 3 PR 3C-1): the pipeline runs per loaded interval, a `gap` row between.
describe('buildIntervalItems (island + suffix)', () => {
  const DAY = 86400
  const baseCtx = { sessionId: SID, legEntries: [], getTaskIdForLaunchToolUse: () => null, isHookMessageAttached: () => false }
  const gap = { fromCursor: 'b:100', toCursor: 'b:900', approxBytes: 800 }
  const run = (island, suffix, extra = {}) => buildIntervalItems({
    ...baseCtx,
    intervals: [
      { messages: island, toolCalls: [], hasMoreBefore: true, hasMoreAfter: true },
      { messages: suffix, toolCalls: [], hasMoreBefore: true, hasMoreAfter: false },
    ],
    gap,
    ...extra,
  })

  it('a single interval is exactly buildDisplayableItems', () => {
    const messages = [user('u1', 0), assistant('a1', 1), user('u2', 2)]
    const single = buildIntervalItems({ ...baseCtx, intervals: [{ messages, toolCalls: [], hasMoreBefore: false, hasMoreAfter: false }], gap: null })
    expect(single).toEqual(build(messages))
  })

  it('puts one gap row between the intervals with a stable key', () => {
    const items = run([user('u1', 0), assistant('a1', 1)], [user('u9', 100), assistant('a9', 101)])
    const gaps = items.filter(i => i.type === 'gap')
    expect(gaps).toHaveLength(1)
    expect(gaps[0]).toMatchObject({ fromCursor: 'b:100', toCursor: 'b:900', approxBytes: 800, key: 'gap:b:100' })
    expect(getItemKey(gaps[0], 0)).toBe('gap:b:100')
    const types = items.map(i => i.type)
    const g = types.indexOf('gap')
    expect(items.slice(0, g).some(i => i.message?.message_id === 'u1')).toBe(true)
    expect(items.slice(g + 1).some(i => i.message?.message_id === 'u9')).toBe(true)
  })

  it('never groups or merges across the gap: assistant turns on both sides stay separate rows', () => {
    const items = run([user('u1', 0), assistant('a1', 1)], [assistant('a9', 100), user('u9', 101)])
    const rows = items.filter(i => i.type === 'message')
    // a1 (island) and a9 (suffix) would merge if the pipeline crossed the gap
    const merged = rows.filter(r => (r.mergedMessages || []).length > 0)
    expect(merged).toHaveLength(0)
    expect(rows.map(r => r.message.message_id)).toEqual(['u1', 'a1', 'a9', 'u9'])
  })

  it('a content-less tool assistant at the head of the suffix keeps its own tools (it does not attach to the island)', () => {
    const tool = (id, sec) => ({ type: 'assistant', message_id: id, content: '', timestamp: ts(sec), metadata: { has_tool_uses: true, tool_uses: [{ id: `t-${id}`, name: 'Bash' }] } })
    const items = run([user('u1', 0), assistant('a1', 1)], [tool('ta', 100), assistant('a9', 101)])
    const island = items.find(i => i.message?.message_id === 'a1')
    expect(island.attachedTools).toEqual([])
    expect(items.some(i => i.message?.message_id === 'ta')).toBe(true)
  })

  it('shows a date separator on both sides of the gap when the days differ and when they do not (each interval starts its own)', () => {
    const sameDay = run([user('u1', 0)], [user('u9', 60)])
    expect(sameDay.filter(i => i.type === 'date_separator')).toHaveLength(2) // one per interval
    const otherDay = run([user('u1', 0)], [user('u9', 3 * DAY)])
    expect(otherDay.filter(i => i.type === 'date_separator')).toHaveLength(2)
    const g = otherDay.findIndex(i => i.type === 'gap')
    expect(otherDay[g + 1].type).toBe('date_separator')
  })

  it('detects compaction groups inside an interval; a group cut at the island end is plain rows, never a crash', () => {
    const sys = (id, sec, metadata) => ({ type: 'system', message_id: id, content: '', timestamp: ts(sec), metadata })
    const full = [
      sys('c-compacting', 10, { subtype: 'status', init_data: { status: 'compacting' } }),
      sys('c-idle', 11, { subtype: 'status', init_data: { status: null } }),
      sys('c-boundary', 12, { subtype: 'compact_boundary' }),
      user('c-cont', 13, 'This session is being continued from a previous conversation that ran out of context.'),
    ]
    const whole = run([user('u1', 0), ...full], [user('u9', 100)])
    expect(whole.filter(i => i.type === 'compaction')).toHaveLength(1)
    const cut = run([user('u1', 0), full[0], full[1]], [user('u9', 100)])
    expect(() => cut.map(i => i.type)).not.toThrow()
    expect(cut.filter(i => i.type === 'compaction')).toHaveLength(0)
  })

  describe('leg signals are scoped to each interval\'s own time span', () => {
    const leg = (taskId, endedAt) => ({
      task_id: taskId, session_id: SID,
      legs: [{ status: 'completed', ended_at: endedAt, description: taskId, result: 'done', tool_use_id: `tu-${taskId}` }],
    })
    const signalsIn = (items, from, to) => items.slice(from, to).filter(i => i.type === 'subagent_signal').map(i => i.taskId)

    it('a signal inside the island span lands in the island; one in the gap, before the island, or before the suffix is dropped; one inside the suffix span stays', () => {
      const legEntries = [leg('before-island', ts(-50)), leg('in-island', ts(1)), leg('in-gap', ts(50)), leg('in-suffix', ts(101))]
      const items = run([user('u1', 0), assistant('a1', 2)], [user('u9', 100), assistant('a9', 102)], { legEntries })
      const g = items.findIndex(i => i.type === 'gap')
      expect(signalsIn(items, 0, g)).toEqual(['in-island'])
      expect(signalsIn(items, g + 1, items.length)).toEqual(['in-suffix'])
    })

    it('the live suffix keeps signals after its last item (open end), the island does not', () => {
      const legEntries = [leg('after-suffix', ts(500)), leg('after-island', ts(20))]
      const items = run([user('u1', 0), assistant('a1', 2)], [user('u9', 100), assistant('a9', 102)], { legEntries })
      const g = items.findIndex(i => i.type === 'gap')
      expect(signalsIn(items, 0, g)).toEqual([])
      expect(signalsIn(items, g + 1, items.length)).toEqual(['after-suffix'])
    })
  })

  it('open permission prompts are anchored in the suffix only, never in an island', () => {
    const waiting = { id: 'tool-wait', name: 'Bash', status: 'permission_required', messageId: 'turn-none', timestamp: ts(5), input: {} }
    const items = buildIntervalItems({
      ...baseCtx,
      intervals: [
        { messages: [user('u1', 0), assistant('a1', 1)], toolCalls: [waiting], hasMoreBefore: true, hasMoreAfter: true },
        { messages: [user('u9', 100), assistant('a9', 101)], toolCalls: [], hasMoreBefore: true, hasMoreAfter: false },
      ],
      gap,
    })
    expect(items.some(i => (i.orphanedPermissionTools || []).length > 0)).toBe(false)
  })
})

describe('no-island pipeline is unchanged (issue #2030 stage 3 PR 3C-1)', () => {
  it('buildDisplayableItems output does not depend on the interval fields when there is one interval', () => {
    const messages = [user('u1', 0), assistant('a1', 1), user('u2', 2), assistant('a2', 3)]
    const plain = build(messages)
    expect(build(messages, { openBefore: false, openAfter: false })).toEqual(plain)
    // an open head only drops signals preceding the first item; with no signals nothing changes
    expect(build(messages, { openBefore: true })).toEqual(plain)
  })
})
