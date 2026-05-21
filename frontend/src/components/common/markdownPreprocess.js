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
