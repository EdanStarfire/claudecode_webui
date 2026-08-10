/**
 * Shared agent identifier resolution for jump-links between agents in a project.
 * Used by SendCommToolHandler.vue (outbound comm recipient) and the agentMention
 * comark plugin (markdown mentions).
 */

const REGEX_SPECIAL_CHARS_RE = /[.*+?^${}()|[\]\\]/g

function escapeRegExp(str) {
  return str.replace(REGEX_SPECIAL_CHARS_RE, '\\$&')
}

/**
 * Resolve an identifier (as an agent would type it, e.g. `to_minion_name`) against a
 * list of agents. Slug-first, then display-name fallback — mirrors the backend's
 * get_minion_by_name_in_legion() resolution order (case-sensitive exact match).
 */
export function resolveAgentByIdentifier(identifier, agents) {
  if (!identifier || !agents) return null
  const bySlug = agents.find(a => a.slug && a.slug === identifier)
  if (bySlug) return bySlug
  return agents.find(a => a.name === identifier) || null
}

/**
 * Build a case-insensitive, word-boundary matcher over an agent list's slugs and
 * display names. Alternatives are sorted longest-first so a shorter identifier can't
 * shadow a longer one that contains it as a prefix (e.g. "bot" vs "botmaster").
 *
 * Returns null when there are no identifiers to match.
 */
export function buildAgentMentionMatcher(agents) {
  if (!agents || agents.length === 0) return null

  const lookup = new Map() // lowercased identifier -> agent id
  const identifiers = new Set()
  for (const agent of agents) {
    if (agent.slug) {
      identifiers.add(agent.slug)
      const key = agent.slug.toLowerCase()
      if (!lookup.has(key)) lookup.set(key, agent.id)
    }
    if (agent.name) {
      identifiers.add(agent.name)
      const key = agent.name.toLowerCase()
      if (!lookup.has(key)) lookup.set(key, agent.id)
    }
  }
  if (identifiers.size === 0) return null

  const sorted = [...identifiers].sort((a, b) => b.length - a.length)
  const pattern = sorted.map(escapeRegExp).join('|')
  const regex = new RegExp(`\\b(${pattern})\\b`, 'gi')

  return {
    regex,
    resolve: (matchedText) => lookup.get(matchedText.toLowerCase()) || null,
  }
}

/**
 * Resolve the current project's agent list and self-identity for mention matching,
 * given a session store (Pinia) and an optional selfAgentId (the session authoring
 * the content being rendered). Falls back to the store's globally selected session
 * when no selfAgentId is supplied. Returns null outside project context.
 */
export function getProjectAgentsContext(sessionStore, selfAgentId) {
  const viewedId = selfAgentId || sessionStore.currentSessionId
  const viewedSession = viewedId ? sessionStore.sessions.get(viewedId) : null
  const projectId = viewedSession?.project_id
  if (!projectId) return null

  const agents = sessionStore.sessionsInProject(projectId).value.map(s => ({
    id: s.session_id,
    slug: s.slug,
    name: s.name,
  }))

  return { projectId, agents, selfId: selfAgentId || null }
}
