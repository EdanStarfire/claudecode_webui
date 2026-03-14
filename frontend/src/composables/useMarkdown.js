import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { getAuthToken } from '@/utils/api'

// Configure marked once
marked.setOptions({
  breaks: true,
  gfm: true
})

// Append auth token to resource API image URLs so <img> tags can load them
const RESOURCE_URL_RE = /(<img\s[^>]*src=")(\/api\/sessions\/[^/]+\/resources\/[^"?]+)(")/g

export function renderMarkdown(content) {
  if (!content) return ''
  let html = marked.parse(content)
  html = html.replace(/\n</g, '<')
  html = html.replace(/\n+$/, '')
  html = DOMPurify.sanitize(html)
  const token = getAuthToken()
  if (token) {
    html = html.replace(RESOURCE_URL_RE, `$1$2?token=${encodeURIComponent(token)}$3`)
  }
  return html
}

/**
 * Reactive composable: accepts a ref/computed of raw markdown,
 * returns a computed of sanitized HTML.
 */
export function useMarkdown(contentRef) {
  const renderedHtml = computed(() => renderMarkdown(contentRef.value))
  return { renderedHtml }
}
