import { createPatch } from 'diff'

/**
 * Build parsed diff lines from old/new strings.
 * Returns { lines: [{type: 'added'|'removed'|'context'|'hunk', content}], added, removed }
 */
export function buildEditDiff(filePath, oldString, newString, contextLines = 3) {
  const patch = createPatch(
    filePath || 'file',
    oldString || '',
    newString || '',
    '',
    '',
    { context: contextLines }
  )

  const rawLines = patch.split('\n')
  const lines = []
  let added = 0
  let removed = 0

  // Skip 4 patch header lines
  for (let i = 4; i < rawLines.length; i++) {
    const line = rawLines[i]
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

  return { lines, added, removed }
}
