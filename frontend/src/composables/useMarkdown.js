import breaks from 'comark/plugins/breaks'
import security from 'comark/plugins/security'
import mermaid from 'comark/plugins/mermaid'
import { resourceTokenPlugin } from './comarkPlugins/resourceToken'
import { externalLinksPlugin } from './comarkPlugins/externalLinks'
import { getAuthToken } from '@/utils/api'
import { useResourceStore } from '@/stores/resource'
import MermaidWrapper from '@/components/common/MermaidWrapper.vue'

const SECURITY_CONFIG = {
  blockedTags: ['script', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'style', 'form', 'input', 'button'],
  allowedProtocols: ['http', 'https', 'mailto', 'tel'],
  allowDataImages: true,
}

const PLUGINS = [
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

export function useMarkdownPlugins() { return PLUGINS }
export function useMarkdownComponents() { return COMPONENTS }
