import { describe, it, expect } from 'vitest'
import { buildDisplayableItems, getItemKey, computeItemKeys } from '../displayableItems'

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

describe('leg-terminal signals across the window boundary (issue #2030, plan §5 exception 1)', () => {
  const leg = (taskId, endedAt) => ({
    task_id: taskId, session_id: SID,
    legs: [{ status: 'completed', ended_at: endedAt, description: taskId, result: 'done', tool_use_id: `tu-${taskId}` }],
  })

  it('drops terminal signals that precede the first loaded item while history is incomplete', () => {
    const messages = [user('u1', 100), assistant('a1', 101)]
    const legEntries = [leg('old-1', ts(1)), leg('old-2', ts(2)), leg('old-3', ts(3)), leg('inside', ts(100.5))]
    const partial = build(messages, { legEntries, historyComplete: false })
    const keys = partial.filter(i => i.type === 'subagent_signal').map(i => i.taskId)
    expect(keys).toEqual(['inside'])
    // Once the history is complete the pre-window signals are placed normally again.
    const complete = build(messages, { legEntries, historyComplete: true })
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
