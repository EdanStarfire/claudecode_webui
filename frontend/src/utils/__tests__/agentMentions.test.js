import { describe, it, expect } from 'vitest'
import {
  resolveAgentByIdentifier,
  buildAgentMentionMatcher,
  getProjectAgentsContext,
} from '../agentMentions.js'

const AGENTS = [
  { id: 'sess-1', slug: 'database-optimizer', name: 'Database Optimizer' },
  { id: 'sess-2', slug: 'bot', name: 'Bot' },
  { id: 'sess-3', slug: 'botmaster', name: 'Bot Master' },
]

describe('resolveAgentByIdentifier', () => {
  it('resolves by slug first', () => {
    expect(resolveAgentByIdentifier('database-optimizer', AGENTS)?.id).toBe('sess-1')
  })

  it('falls back to display name', () => {
    expect(resolveAgentByIdentifier('Database Optimizer', AGENTS)?.id).toBe('sess-1')
  })

  it('is case-sensitive', () => {
    expect(resolveAgentByIdentifier('DATABASE-OPTIMIZER', AGENTS)).toBeNull()
  })

  it('returns null for unresolvable identifiers', () => {
    expect(resolveAgentByIdentifier('nonexistent-agent', AGENTS)).toBeNull()
  })

  it('returns null for empty/missing input', () => {
    expect(resolveAgentByIdentifier('', AGENTS)).toBeNull()
    expect(resolveAgentByIdentifier(null, AGENTS)).toBeNull()
  })
})

describe('buildAgentMentionMatcher', () => {
  it('returns null when there are no agents', () => {
    expect(buildAgentMentionMatcher([])).toBeNull()
    expect(buildAgentMentionMatcher(null)).toBeNull()
  })

  it('matches slug and display name, case-insensitively', () => {
    const matcher = buildAgentMentionMatcher(AGENTS)
    matcher.regex.lastIndex = 0
    expect(matcher.regex.test('ask database-optimizer for help')).toBe(true)
    matcher.regex.lastIndex = 0
    expect(matcher.regex.test('ask DATABASE-OPTIMIZER for help')).toBe(true)
    matcher.regex.lastIndex = 0
    expect(matcher.regex.test('ask Database Optimizer for help')).toBe(true)
  })

  it('does not match a substring inside an unrelated word (word boundaries)', () => {
    const matcher = buildAgentMentionMatcher([{ id: 'sess-4', slug: 'sage', name: 'Sage' }])
    matcher.regex.lastIndex = 0
    expect(matcher.regex.test('send me a message please')).toBe(false)
  })

  it('resolves the longer identifier when one is a prefix of another', () => {
    const matcher = buildAgentMentionMatcher(AGENTS)
    matcher.regex.lastIndex = 0
    const match = matcher.regex.exec('ping botmaster now')
    expect(match[1]).toBe('botmaster')
    expect(matcher.resolve(match[1])).toBe('sess-3')
  })

  it('still matches the shorter identifier on its own', () => {
    const matcher = buildAgentMentionMatcher(AGENTS)
    matcher.regex.lastIndex = 0
    const match = matcher.regex.exec('ping bot now')
    expect(match[1]).toBe('bot')
    expect(matcher.resolve(match[1])).toBe('sess-2')
  })

  it('resolve() is case-insensitive lookup back to the agent id', () => {
    const matcher = buildAgentMentionMatcher(AGENTS)
    expect(matcher.resolve('DATABASE-OPTIMIZER')).toBe('sess-1')
  })
})

describe('getProjectAgentsContext', () => {
  function makeSessionStore({ currentSessionId, sessions }) {
    const sessionsMap = new Map(sessions.map(s => [s.session_id, s]))
    return {
      currentSessionId,
      sessions: sessionsMap,
      sessionsInProject: (projectId) => ({
        value: sessions.filter(s => s.project_id === projectId),
      }),
    }
  }

  const SESSIONS = [
    { session_id: 'sess-1', project_id: 'proj-1', slug: 'alpha', name: 'Alpha' },
    { session_id: 'sess-2', project_id: 'proj-1', slug: 'beta', name: 'Beta' },
    { session_id: 'sess-3', project_id: 'proj-2', slug: 'gamma', name: 'Gamma' },
  ]

  it('resolves project + agents from an explicit selfAgentId', () => {
    const store = makeSessionStore({ currentSessionId: null, sessions: SESSIONS })
    const ctx = getProjectAgentsContext(store, 'sess-1')
    expect(ctx.projectId).toBe('proj-1')
    expect(ctx.agents.map(a => a.id).sort()).toEqual(['sess-1', 'sess-2'])
    expect(ctx.selfId).toBe('sess-1')
  })

  it('falls back to the store current session when no selfAgentId given', () => {
    const store = makeSessionStore({ currentSessionId: 'sess-3', sessions: SESSIONS })
    const ctx = getProjectAgentsContext(store, null)
    expect(ctx.projectId).toBe('proj-2')
    expect(ctx.selfId).toBeNull()
  })

  it('returns null outside project context (no resolvable session)', () => {
    const store = makeSessionStore({ currentSessionId: null, sessions: SESSIONS })
    expect(getProjectAgentsContext(store, null)).toBeNull()
    expect(getProjectAgentsContext(store, 'nonexistent-session')).toBeNull()
  })
})
