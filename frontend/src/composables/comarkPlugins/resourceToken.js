import { defineComarkPlugin } from 'comark'
import { visit } from 'comark/utils'

const RESOURCE_URL_RE = /^\/api\/sessions\/[^/]+\/resources\/[^/?]+$/
const RESOURCE_EXTRACT_RE = /^\/api\/sessions\/([^/]+)\/resources\/([^/?]+)$/

export const resourceTokenPlugin = defineComarkPlugin((opts = {}) => ({
  name: 'resource-token',
  post(state) {
    const token = opts.getToken?.()
    if (!token) return
    const suffix = `?token=${encodeURIComponent(token)}`
    const getResource = opts.getResource
    visit(
      state.tree,
      (node) => Array.isArray(node) && (node[0] === 'a' || node[0] === 'img'),
      (node) => {
        const attrs = node[1] || {}
        const attrName = node[0] === 'a' ? 'href' : 'src'
        const url = attrs[attrName]
        if (typeof url === 'string' && RESOURCE_URL_RE.test(url)) {
          const tokenizedUrl = url + suffix

          // For img nodes, check if the referenced resource is a video
          if (node[0] === 'img' && getResource) {
            const match = RESOURCE_EXTRACT_RE.exec(url)
            if (match) {
              const [, sessionId, resourceId] = match
              const resource = getResource(sessionId, resourceId)
              if (resource) {
                const mimeType = (resource.mime_type || '').toLowerCase()
                const isVideo = resource.is_video === true || mimeType.startsWith('video/')
                if (isVideo) {
                  node[0] = 'video'
                  node[1] = { src: tokenizedUrl, controls: '', preload: 'metadata' }
                  return
                }
              }
            }
          }

          attrs[attrName] = tokenizedUrl
          node[1] = attrs
        }
      }
    )
  },
}))
