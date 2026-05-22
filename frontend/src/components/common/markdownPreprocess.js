// comark's Comark.vue component trims markdown before parsing, so prepending \n to shift
// `---` off line 0 is ineffective. Instead, replace the leading thematic break with `* * *`
// which is semantically identical but bypasses comark's frontmatter detector entirely.
//
// Pipeline: autoCloseMarkdown → parseFrontmatter. autoClose sees `---` on line 0,
// sets inFrontmatter=true, appends \n--- at EOF. parseFrontmatter then finds a properly
// delimited block and strips the entire content as YAML, returning empty string.
// Replacing `---\n` with `* * *\n` before comark sees it prevents this chain.
export function preprocessLeadingThematicBreak(content) {
  if (typeof content !== 'string') return content
  if (!content.startsWith('---\n')) return content

  const rest = content.slice(4)
  if (/\n---(\n|$)/.test(rest)) return content // genuine frontmatter — leave alone

  return '* * *\n' + rest
}

// Module-level with g flag — lastIndex must be reset at the start of each call.
const CODE_SEGMENT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*?`)/g
const INDENTED_CODE = /^ {4,}\S/

function escapeInProse(text) {
  if (!text) return text
  return text.split('\n').map(line => {
    if (!line.includes(':')) return line
    if (INDENTED_CODE.test(line)) return line
    return line.replace(/(^|[^A-Za-z0-9/])(:\d)/g, '$1\\$2')
  }).join('\n')
}

export function preprocessPortColons(content) {
  if (typeof content !== 'string') return content
  if (!content.includes(':')) return content

  let out = ''
  let lastIndex = 0
  let m
  CODE_SEGMENT.lastIndex = 0
  while ((m = CODE_SEGMENT.exec(content)) !== null) {
    out += escapeInProse(content.slice(lastIndex, m.index))
    out += m[0]
    lastIndex = CODE_SEGMENT.lastIndex
  }
  out += escapeInProse(content.slice(lastIndex))
  return out
}
