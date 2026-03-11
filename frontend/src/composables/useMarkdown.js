import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

// Configure marked once
marked.setOptions({
  breaks: true,
  gfm: true
})

/**
 * Render a markdown string to sanitized HTML.
 * Shared logic extracted from 7 components.
 */
export function renderMarkdown(content) {
  if (!content) return ''
  let html = marked.parse(content)
  html = html.replace(/\n</g, '<')
  html = html.replace(/\n+$/, '')
  return DOMPurify.sanitize(html)
}

/**
 * Reactive composable: accepts a ref/computed of raw markdown,
 * returns a computed of sanitized HTML.
 */
export function useMarkdown(contentRef) {
  const renderedHtml = computed(() => renderMarkdown(contentRef.value))
  return { renderedHtml }
}
