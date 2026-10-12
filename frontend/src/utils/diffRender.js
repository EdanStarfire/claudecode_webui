import { createPatch } from 'diff'

function stripPatchHeader(filePath, oldString, newString, contextLines) {
  const patch = createPatch(
    filePath || 'file',
    oldString || '',
    newString || '',
    '',
    '',
    { context: contextLines }
  )
  return patch.split('\n').slice(4)
}

/**
 * Build parsed diff lines from old/new strings.
 * Returns { lines: [{type: 'added'|'removed'|'context'|'hunk', content}], added, removed, text }
 * `text` is the same header-stripped unified diff buildUnifiedDiffText() returns, computed
 * from the same createPatch() call so callers needing both don't pay for it twice.
 */
export function buildEditDiff(filePath, oldString, newString, contextLines = 3) {
  const rawLines = stripPatchHeader(filePath, oldString, newString, contextLines)
  const lines = []
  let added = 0
  let removed = 0

  for (const line of rawLines) {
    if (!line) continue
    if (line.startsWith('@@')) {
      lines.push({ type: 'hunk', content: line })
    } else if (line.startsWith('+')) {
      lines.push({ type: 'added', content: line.slice(1) })
      added++
    } else if (line.startsWith('-')) {
      lines.push({ type: 'removed', content: line.slice(1) })
      removed++
    } else {
      lines.push({ type: 'context', content: line.startsWith(' ') ? line.slice(1) : line })
    }
  }

  return { lines, added, removed, text: rawLines.join('\n') }
}

/**
 * Build a header-stripped unified diff string from old/new strings, suitable
 * for feeding into DiffFullView's own unified-diff parser.
 */
export function buildUnifiedDiffText(filePath, oldString, newString, contextLines = 3) {
  return stripPatchHeader(filePath, oldString, newString, contextLines).join('\n')
}

/**
 * Resolve which sibling version to diff a resource version against, and in
 * which order. `old`/`new` are always ordered by ascending version_number
 * regardless of which button (vs-previous or vs-latest) triggered the call,
 * so the label and diff always read chronologically forward.
 * Returns null when there is no valid sibling to diff against.
 */
export function resolveVersionDiffPair(resource, version, direction) {
  if (!resource || !version) return null

  let sibling = null
  if (direction === 'previous') {
    if (version.version_number <= 1) return null
    sibling = resource.versions?.find(v => v.version_number === version.version_number - 1)
  } else if (direction === 'latest') {
    if (version.version_number >= resource.version_count) return null
    sibling = resource.versions?.find(v => v.version_number === resource.version_count)
  }
  if (!sibling) return null

  const [older, newer] = version.version_number < sibling.version_number
    ? [version, sibling]
    : [sibling, version]

  const name = resource.original_filename || resource.title || 'resource'
  return {
    oldResourceId: older.resource_id,
    newResourceId: newer.resource_id,
    oldVersionNumber: older.version_number,
    newVersionNumber: newer.version_number,
    label: `${name} — v${older.version_number} → v${newer.version_number}`
  }
}
