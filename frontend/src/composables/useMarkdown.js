import breaks from 'comark/plugins/breaks'
import security from 'comark/plugins/security'
import mermaid from 'comark/plugins/mermaid'
import { resourceTokenPlugin } from './comarkPlugins/resourceToken'
import { externalLinksPlugin } from './comarkPlugins/externalLinks'
import { agentMentionPlugin } from './comarkPlugins/agentMention'
import { getAuthToken } from '@/utils/api'
import { useResourceStore } from '@/stores/resource'
import { useSessionStore } from '@/stores/session'
import { getProjectAgentsContext } from '@/utils/agentMentions'
import MermaidWrapper from '@/components/common/MermaidWrapper.vue'

const SECURITY_CONFIG = {
  blockedTags: ['script', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'style', 'form', 'input', 'button'],
  allowedProtocols: ['http', 'https', 'mailto', 'tel'],
  allowDataImages: true,
}

const STATIC_PLUGINS = [
  breaks(),
  security(SECURITY_CONFIG),
  mermaid(),
  resourceTokenPlugin({
    getToken: getAuthToken,
    // Lazy store access inside the closure so it runs after Pinia is initialized
    getResource: (sessionId, resourceId) => useResourceStore().getResourceById(sessionId, resourceId),
  }),
  externalLinksPlugin(),
]

const COMPONENTS = { mermaid: MermaidWrapper }

// selfAgentId identifies which agent authored the content being rendered (when known,
// e.g. a comm's sender/recipient) so agentMentionPlugin can skip self-mentions.
// No-ops cleanly outside project/session context (settings pages, templates, etc).
export function useMarkdownPlugins({ selfAgentId = null } = {}) {
  return [
    ...STATIC_PLUGINS,
    agentMentionPlugin({
      // Lazy store access inside the closure so it runs after Pinia is initialized
      getProjectAgents: () => getProjectAgentsContext(useSessionStore(), selfAgentId),
    }),
  ]
}
export function useMarkdownComponents() { return COMPONENTS }
