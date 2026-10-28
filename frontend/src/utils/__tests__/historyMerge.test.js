import { describe, it, expect } from 'vitest'
import { mergeToolCallFields, mergeOlderPage } from '../historyMerge'

function tool(id, overrides = {}) {
  return {
    id, name: 'Write', input: {}, status: 'pending', result: null, timestamp: 1, messageId: null,
    parent_tool_use_id: null, permissionRequestId: undefined, permissionDecision: null, suggestions: [],
    appliedUpdates: [], autoApprovedReason: null, agentId: null, senderAttachments: null, answers: null,
    isExpanded: true,
    ...overrides,
  }
}

function target(messages = [], toolCalls = [], retryIndex = new Map()) {
  const messageIndex = new Map()
  messages.forEach((m, i) => messageIndex.set(m.message_id, i))
  const toolIndex = new Map()
  toolCalls.forEach((t, i) => toolIndex.set(t.id, i))
  return { messages, messageIndex, toolCalls, toolIndex, retryIndex }
}

describe('mergeToolCallFields (issue #2030)', () => {
  it('keeps the newer entry\'s input when the older record only has the pre-streaming {}', () => {
    const current = tool('t1', { input: { file_path: '/a', content: 'x' }, status: 'completed' })
    mergeToolCallFields(current, tool('t1', { input: {} }))
    expect(current.input).toEqual({ file_path: '/a', content: 'x' })
    expect(current.status).toBe('completed')
  })

  it('fills fields the newer entry lacks from the older record', () => {
    const current = tool('t1', { input: {}, status: 'completed', timestamp: null, messageId: null })
    mergeToolCallFields(current, tool('t1', {
      input: { a: 1 }, timestamp: 5, messageId: 'turn-1', permissionRequestId: 'req-1',
      permissionDecision: 'allow', suggestions: [{ x: 1 }], parent_tool_use_id: 'p',
    }))
    expect(current).toMatchObject({
      input: { a: 1 }, timestamp: 5, messageId: 'turn-1', permissionRequestId: 'req-1',
      permissionDecision: 'allow', suggestions: [{ x: 1 }], parent_tool_use_id: 'p',
    })
  })

  it('never regresses status and preserves the orphaned / permission locks', () => {
    const current = tool('t1', { status: 'completed', _isOrphaned: true, _orphanedInfo: { reason: 'denied' }, permissionDecision: 'deny', isExpanded: false })
    mergeToolCallFields(current, tool('t1', { status: 'pending', permissionDecision: 'allow', isExpanded: true }))
    expect(current.status).toBe('completed')
    expect(current._isOrphaned).toBe(true)
    expect(current.permissionDecision).toBe('deny')
    expect(current.isExpanded).toBe(false)
  })
})

describe('mergeOlderPage (issue #2030)', () => {
  const msg = (id) => ({ message_id: id, type: 'user' })

  it('puts older messages first and drops duplicates already loaded', () => {
    const current = target([msg('c'), msg('d')])
    const older = target([msg('a'), msg('b'), msg('c')])
    const merged = mergeOlderPage(current, older)
    expect(merged.messages.map(m => m.message_id)).toEqual(['a', 'b', 'c', 'd'])
    expect(merged.prependedMessageCount).toBe(2)
    expect([...merged.messageIndex]).toEqual([['a', 0], ['b', 1], ['c', 2], ['d', 3]])
  })

  it('orders tools by first appearance and keeps the current entry object for overlaps', () => {
    const shared = tool('shared', { status: 'completed', input: { full: true }, isExpanded: false })
    const current = target([], [shared, tool('newer')])
    const older = target([], [tool('oldest'), tool('shared', { input: {} })])
    const merged = mergeOlderPage(current, older)
    expect(merged.toolCalls.map(t => t.id)).toEqual(['oldest', 'shared', 'newer'])
    expect(merged.toolCalls[1]).toBe(shared)
    expect(shared.input).toEqual({ full: true })
    expect([...merged.toolIndex]).toEqual([['oldest', 0], ['shared', 1], ['newer', 2]])
  })

  it('shifts retry indices by the number of prepended messages', () => {
    const current = target([msg('c')], [], new Map([['retry-1', 0]]))
    const merged = mergeOlderPage(current, target([msg('a'), msg('b')]))
    expect(merged.retryIndex.get('retry-1')).toBe(2)
  })

  it('does not reuse the input arrays', () => {
    const current = target([msg('c')])
    const merged = mergeOlderPage(current, target([]))
    expect(merged.messages).not.toBe(current.messages)
  })
})
