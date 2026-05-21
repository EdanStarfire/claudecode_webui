import breaks from 'comark/plugins/breaks'
import security from 'comark/plugins/security'
import mermaid from 'comark/plugins/mermaid'
import { resourceTokenPlugin } from './comarkPlugins/resourceToken'
import { externalLinksPlugin } from './comarkPlugins/externalLinks'
import { getAuthToken } from '@/utils/api'
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
  resourceTokenPlugin({ getToken: getAuthToken }),
  externalLinksPlugin(),
]

const COMPONENTS = { mermaid: MermaidWrapper }

export function useMarkdownPlugins() { return PLUGINS }
export function useMarkdownComponents() { return COMPONENTS }
