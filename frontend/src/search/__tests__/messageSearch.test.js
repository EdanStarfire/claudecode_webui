import { describe, it, expect } from 'vitest'
import { findMatches, MIN_QUERY_LENGTH } from '@/search/messageSearch'

function makeFakeStore({ messages = [], toolCalls = [] } = {}) {
  const messagesBySession = new Map([['s1', messages]])
  const toolCallsBySession = new Map([['s1', toolCalls]])
  const toolIndexBySession = new Map([
    ['s1', new Map(toolCalls.map((tc, i) => [tc.id, i]))],
  ])
  return { messagesBySession, toolCallsBySession, toolIndexBySession }
}

describe('messageSearch.findMatches', () => {
  it('matches plain message content case-insensitively', () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: 'Hello World', metadata: {} }],
    })
    const matches = findMatches('s1', 'world', { messageStore })
    expect(matches).toHaveLength(1)
    expect(matches[0]).toMatchObject({ messageId: 'm1', field: 'content', matchStart: 6, matchLength: 5 })
  })

  it('matches thinking_content', () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: '', metadata: { thinking_content: 'pondering apples' } }],
    })
    const matches = findMatches('s1', 'apples', { messageStore })
    expect(matches).toHaveLength(1)
    expect(matches[0].field).toBe('thinking')
  })

  it('matches tool name, input, and result content via tool_uses resolution', () => {
    const messageStore = makeFakeStore({
      messages: [
        {
          id: 'm1',
          content: '',
          metadata: { tool_uses: [{ id: 't1' }] },
        },
      ],
      toolCalls: [
        {
          id: 't1',
          name: 'BashTool',
          input: { command: 'ls needle' },
          result: { content: 'found the needle here' },
        },
      ],
    })

    const nameMatches = findMatches('s1', 'bashtool', { messageStore })
    expect(nameMatches).toEqual([expect.objectContaining({ toolId: 't1', field: 'tool_name' })])

    const inputMatches = findMatches('s1', 'needle', { messageStore })
    const fields = inputMatches.map(m => m.field)
    expect(fields).toContain('tool_input')
    expect(fields).toContain('tool_result')
    inputMatches.forEach(m => expect(m.toolId).toBe('t1'))
  })

  it('is case-insensitive', () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: 'MiXeD CaSe TeXt', metadata: {} }],
    })
    expect(findMatches('s1', 'mixed case', { messageStore })).toHaveLength(1)
  })

  it(`requires at least ${MIN_QUERY_LENGTH} characters`, () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: 'a lone letter a', metadata: {} }],
    })
    expect(findMatches('s1', 'a', { messageStore })).toEqual([])
    expect(findMatches('s1', '  a ', { messageStore })).toEqual([])
  })

  it('skips non-string content defensively instead of stringifying it', () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: [{ type: 'text', text: 'needle' }], metadata: {} }],
    })
    expect(() => findMatches('s1', 'needle', { messageStore })).not.toThrow()
    expect(findMatches('s1', 'needle', { messageStore })).toEqual([])
  })

  it('returns matches in chronological raw-store order, not display-grouped order', () => {
    const messageStore = makeFakeStore({
      messages: [
        { id: 'm1', content: 'needle one', metadata: {} },
        { id: 'm2', content: 'needle two', metadata: {} },
      ],
    })
    const matches = findMatches('s1', 'needle', { messageStore })
    expect(matches.map(m => m.messageId)).toEqual(['m1', 'm2'])
  })

  it('finds multiple occurrences within the same field, numbering them via occurrenceIndex', () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: 'cat cat cat', metadata: {} }],
    })
    const matches = findMatches('s1', 'cat', { messageStore })
    expect(matches).toHaveLength(3)
    expect(matches.map(m => m.matchStart)).toEqual([0, 4, 8])
    expect(matches.map(m => m.occurrenceIndex)).toEqual([0, 1, 2])
  })

  it('numbers occurrenceIndex per messageId across multiple fields on the same message', () => {
    const messageStore = makeFakeStore({
      messages: [{ id: 'm1', content: 'needle', metadata: { thinking_content: 'another needle' } }],
    })
    const matches = findMatches('s1', 'needle', { messageStore })
    expect(matches).toHaveLength(2)
    expect(matches.map(m => m.field)).toEqual(['content', 'thinking'])
    expect(matches.map(m => m.occurrenceIndex)).toEqual([0, 1])
  })

  it('restarts occurrenceIndex at 0 for each distinct messageId', () => {
    const messageStore = makeFakeStore({
      messages: [
        { id: 'm1', content: 'needle needle', metadata: {} },
        { id: 'm2', content: 'needle', metadata: {} },
      ],
    })
    const matches = findMatches('s1', 'needle', { messageStore })
    expect(matches.map(m => ({ messageId: m.messageId, occurrenceIndex: m.occurrenceIndex }))).toEqual([
      { messageId: 'm1', occurrenceIndex: 0 },
      { messageId: 'm1', occurrenceIndex: 1 },
      { messageId: 'm2', occurrenceIndex: 0 },
    ])
  })

  it('returns no matches for an empty sessionId or missing store', () => {
    expect(findMatches(null, 'needle', { messageStore: makeFakeStore() })).toEqual([])
    expect(findMatches('s1', 'needle', {})).toEqual([])
  })
})
