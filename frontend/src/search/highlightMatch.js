// Raw-text-aware DOM highlight/unhighlight for the message-list search feature (issue #2012).
// Modeled on SettingsLayout.vue's TreeWalker-based search highlighter. Re-finds the substring
// textually in already-mounted DOM rather than mapping raw-source offsets to DOM offsets, since
// markdown-rendered DOM doesn't preserve 1:1 offsets with raw source.

const HIGHLIGHT_CLASS = 'message-search-highlight'
const SKIP_TAGS = ['script', 'style', 'input', 'textarea', 'button', 'select', 'mark']

export function clearHighlight(container) {
  if (!container) return
  const marks = container.querySelectorAll(`mark.${HIGHLIGHT_CLASS}`)
  marks.forEach(mark => {
    const parent = mark.parentNode
    if (!parent) return
    parent.replaceChild(document.createTextNode(mark.textContent), mark)
    parent.normalize()
  })
}

/**
 * Walks `container`'s text nodes and wraps the `occurrenceIndex`-th (0-based) case-insensitive
 * occurrence of `query`, counted across the whole container in DOM order, in a
 * <mark class="message-search-highlight">. Only ever highlights one occurrence — the caller is
 * responsible for clearing any previous highlight first. `occurrenceIndex` lets a row with
 * several matches (e.g. the query repeated in one field, or present in more than one field)
 * highlight the specific occurrence the user is currently navigating to rather than always the
 * first one found — an approximation, not an exact raw-offset mapping (see module comment).
 */
export function applyHighlight(container, query, occurrenceIndex = 0) {
  if (!container) return false
  const trimmed = (query || '').trim()
  if (!trimmed) return false
  const lowerQuery = trimmed.toLowerCase()

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const tag = node.parentElement?.tagName?.toLowerCase()
      if (SKIP_TAGS.includes(tag)) return NodeFilter.FILTER_REJECT
      if (!node.textContent.toLowerCase().includes(lowerQuery)) return NodeFilter.FILTER_SKIP
      return NodeFilter.FILTER_ACCEPT
    },
  })

  let remaining = occurrenceIndex
  let targetNode = null
  let targetIdx = -1
  let node
  while ((node = walker.nextNode())) {
    const lowerText = node.textContent.toLowerCase()
    let idx = lowerText.indexOf(lowerQuery)
    while (idx !== -1) {
      if (remaining === 0) {
        targetNode = node
        targetIdx = idx
        break
      }
      remaining--
      idx = lowerText.indexOf(lowerQuery, idx + 1)
    }
    if (targetNode) break
  }

  if (!targetNode) return false

  const text = targetNode.textContent
  const before = document.createTextNode(text.slice(0, targetIdx))
  const mark = document.createElement('mark')
  mark.className = HIGHLIGHT_CLASS
  mark.textContent = text.slice(targetIdx, targetIdx + trimmed.length)
  const after = document.createTextNode(text.slice(targetIdx + trimmed.length))

  const parent = targetNode.parentNode
  parent.replaceChild(after, targetNode)
  parent.insertBefore(mark, after)
  parent.insertBefore(before, mark)

  mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return true
}
