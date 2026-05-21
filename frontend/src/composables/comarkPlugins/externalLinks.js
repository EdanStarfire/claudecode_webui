import { defineComarkPlugin } from 'comark'
import { visit } from 'comark/utils'

export const externalLinksPlugin = defineComarkPlugin(() => ({
  name: 'external-links',
  post(state) {
    visit(
      state.tree,
      (node) => Array.isArray(node) && node[0] === 'a',
      (node) => {
        const attrs = node[1] || {}
        const href = attrs.href
        if (typeof href === 'string' && /^https?:\/\//.test(href)) {
          attrs.target = '_blank'
          attrs.rel = 'noopener noreferrer'
          node[1] = attrs
        }
      }
    )
  },
}))
