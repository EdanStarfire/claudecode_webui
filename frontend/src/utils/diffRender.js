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
