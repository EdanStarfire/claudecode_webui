import { defineComarkPlugin } from 'comark'
import { visit } from 'comark/utils'

const RESOURCE_URL_RE = /^\/api\/sessions\/[^/]+\/resources\/[^/?]+$/

export const resourceTokenPlugin = defineComarkPlugin((opts = {}) => ({
  name: 'resource-token',
  post(state) {
    const token = opts.getToken?.()
    if (!token) return
    const suffix = `?token=${encodeURIComponent(token)}`
    visit(
      state.tree,
      (node) => Array.isArray(node) && (node[0] === 'a' || node[0] === 'img'),
      (node) => {
        const attrs = node[1] || {}
        const attrName = node[0] === 'a' ? 'href' : 'src'
        const url = attrs[attrName]
        if (typeof url === 'string' && RESOURCE_URL_RE.test(url)) {
          attrs[attrName] = url + suffix
          node[1] = attrs
        }
      }
    )
  },
}))
