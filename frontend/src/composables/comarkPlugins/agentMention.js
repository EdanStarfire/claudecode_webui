import { defineComarkPlugin } from 'comark'
import { buildAgentMentionMatcher } from '@/utils/agentMentions'

// Tags whose text content should never be scanned for mentions: code/pre (code spans
// and blocks) and 'a' (don't nest a link inside an already-linked node).
const SKIP_TAGS = new Set(['a', 'code', 'pre', 'math', 'script', 'style', 'kbd'])

// Per-project memoization: rebuild the matcher only when a project's agent list
// (ids/slugs/names) actually changes, not on every render pass.
const matcherCache = new Map()

function agentsEqual(a, b) {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id || a[i].slug !== b[i].slug || a[i].name !== b[i].name) return false
  }
  return true
}

function getMatcher(projectId, agents) {
  const cached = matcherCache.get(projectId)
  if (cached && agentsEqual(cached.agents, agents)) return cached.matcher
  const matcher = buildAgentMentionMatcher(agents)
  matcherCache.set(projectId, { agents, matcher })
  return matcher
}

export const agentMentionPlugin = defineComarkPlugin((opts = {}) => ({
  name: 'agent-mention',
  post(state) {
    const getProjectAgents = opts.getProjectAgents
    if (!getProjectAgents) return
    const context = getProjectAgents()
    if (!context || !context.projectId || !context.agents?.length) return

    const matcher = getMatcher(context.projectId, context.agents)
    if (!matcher) return

    const selfId = context.selfId || null

    function splitText(text) {
      matcher.regex.lastIndex = 0
      let match
      let last = 0
      let parts = null
      while ((match = matcher.regex.exec(text)) !== null) {
        const agentId = matcher.resolve(match[1])
        if (!agentId || agentId === selfId) continue
        parts = parts || []
        if (match.index > last) parts.push(text.slice(last, match.index))
        parts.push(['a', { href: `#/session/${agentId}` }, match[1]])
        last = match.index + match[1].length
      }
      if (!parts) return null
      if (last < text.length) parts.push(text.slice(last))
      return parts
    }

    // visit() only supports 1:1 node replacement/removal — it can't fan a single
    // text node out into [text, link, text]. Walk and splice directly instead,
    // mirroring comark's own punctuation.js plugin.
    function walk(nodes, startIndex, skip) {
      let i = startIndex
      while (i < nodes.length) {
        const node = nodes[i]
        if (typeof node === 'string') {
          if (!skip) {
            const parts = splitText(node)
            if (parts) {
              nodes.splice(i, 1, ...parts)
              i += parts.length
              continue
            }
          }
        } else if (Array.isArray(node) && node[0] != null) {
          walk(node, 2, skip || SKIP_TAGS.has(node[0]))
        }
        i += 1
      }
    }

    walk(state.tree.nodes, 0, false)
  },
}))
