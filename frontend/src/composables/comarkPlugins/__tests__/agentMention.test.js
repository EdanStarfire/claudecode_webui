import { describe, it, expect } from 'vitest'
import { agentMentionPlugin } from '../agentMention.js'

function makeState(nodes) {
  return { tree: { nodes, frontmatter: {}, meta: {} } }
}

function runPlugin(nodes, context) {
  const plugin = agentMentionPlugin({ getProjectAgents: () => context })
  const state = makeState(nodes)
  plugin.post(state)
  return state.tree.nodes
}

const AGENTS = [
  { id: 'sess-db', slug: 'database-optimizer', name: 'Database Optimizer' },
  { id: 'sess-report', slug: 'reporter', name: 'Report' },
]

const CONTEXT = { projectId: 'proj-1', agents: AGENTS, selfId: null }

describe('agentMentionPlugin', () => {
  it('links a plain-text mention by slug', () => {
    const nodes = [['p', {}, 'ask database-optimizer for help']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result[0]).toEqual([
      'p',
      {},
      'ask ',
      ['a', { href: '#/session/sess-db' }, 'database-optimizer'],
      ' for help',
    ])
  })

  it('links a mention by display name', () => {
    const nodes = [['p', {}, 'ask Database Optimizer please']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result[0]).toEqual([
      'p',
      {},
      'ask ',
      ['a', { href: '#/session/sess-db' }, 'Database Optimizer'],
      ' please',
    ])
  })

  it('is case-insensitive', () => {
    const nodes = [['p', {}, 'ask DATABASE-OPTIMIZER now']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result[0][3]).toEqual(['a', { href: '#/session/sess-db' }, 'DATABASE-OPTIMIZER'])
  })

  it('does not create false-positive substring matches', () => {
    const nodes = [['p', {}, 'send a message please']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result).toEqual(nodes)
  })

  it('leaves unresolvable/nonexistent agent names as plain text', () => {
    const nodes = [['p', {}, 'ask nonexistent-agent for help']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result).toEqual(nodes)
  })

  it('does not touch text inside code spans', () => {
    const nodes = [['p', {}, ['code', {}, 'database-optimizer']]]
    const result = runPlugin(nodes, CONTEXT)
    expect(result).toEqual(nodes)
  })

  it('does not touch text inside pre/code blocks', () => {
    const nodes = [['pre', {}, ['code', {}, 'call database-optimizer()']]]
    const result = runPlugin(nodes, CONTEXT)
    expect(result).toEqual(nodes)
  })

  it('does not link text already inside an anchor', () => {
    const nodes = [['a', { href: 'https://example.com' }, 'database-optimizer']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result).toEqual(nodes)
  })

  it('skips self-mentions', () => {
    const nodes = [['p', {}, 'I am database-optimizer']]
    const result = runPlugin(nodes, { ...CONTEXT, selfId: 'sess-db' })
    expect(result).toEqual(nodes)
  })

  it('links other mentions while skipping self-mentions in the same message', () => {
    const nodes = [['p', {}, 'database-optimizer, please tell reporter']]
    const result = runPlugin(nodes, { ...CONTEXT, selfId: 'sess-db' })
    expect(result[0]).toEqual([
      'p',
      {},
      'database-optimizer, please tell ',
      ['a', { href: '#/session/sess-report' }, 'reporter'],
    ])
  })

  it('no-ops when getProjectAgents is not provided', () => {
    const nodes = [['p', {}, 'ask database-optimizer']]
    const plugin = agentMentionPlugin({})
    const state = makeState(nodes)
    plugin.post(state)
    expect(state.tree.nodes).toEqual(nodes)
  })

  it('no-ops outside project context (no agents)', () => {
    const nodes = [['p', {}, 'ask database-optimizer']]
    const result = runPlugin(nodes, null)
    expect(result).toEqual(nodes)
  })

  it('no-ops when the agent list is empty', () => {
    const nodes = [['p', {}, 'ask database-optimizer']]
    const result = runPlugin(nodes, { projectId: 'proj-1', agents: [], selfId: null })
    expect(result).toEqual(nodes)
  })

  it('handles multiple mentions in one text node', () => {
    const nodes = [['p', {}, 'database-optimizer and reporter should sync']]
    const result = runPlugin(nodes, CONTEXT)
    expect(result[0]).toEqual([
      'p',
      {},
      ['a', { href: '#/session/sess-db' }, 'database-optimizer'],
      ' and ',
      ['a', { href: '#/session/sess-report' }, 'reporter'],
      ' should sync',
    ])
  })
})
