/**
 * Template variable validation for path configuration fields (issue #917).
 */

export const KNOWN_VARS = new Set(['session_id', 'session_data', 'working_dir'])
export const VALID_VARS_DISPLAY = '{session_id}, {session_data}, {working_dir}'

// Matches complete {word} tokens
const TOKEN_RE = /\{(\w+)\}/g
// Detects lone { or } that are not part of a complete token
const LONE_BRACE_RE = /\{(?!\w+\})|(?<!\{\w*)\}/

/**
 * Validate a single path string for template variable correctness.
 *
 * @param {string|null} value
 * @returns {string|null} Error message, or null if valid.
 */
export function validateTemplatePath(value) {
  if (!value) return null

  // Check for lone braces (partial/malformed syntax)
  if (LONE_BRACE_RE.test(value)) {
    return `Invalid template syntax: unmatched { or } in "${value}". Valid variables: ${VALID_VARS_DISPLAY}`
  }

  // Check all complete tokens are known
  for (const match of value.matchAll(TOKEN_RE)) {
    const name = match[1]
    if (!KNOWN_VARS.has(name)) {
      return `Unknown template variable: {${name}}. Valid variables: ${VALID_VARS_DISPLAY}`
    }
  }

  return null
}

/**
 * Validate a newline-separated list of path strings.
 * Returns the first error found, or null if all lines are valid.
 *
 * @param {string|null} value  Newline-separated mount specs or paths
 * @returns {string|null}
 */
export function validateTemplatePathList(value) {
  if (!value || !value.trim()) return null
  for (const line of value.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const err = validateTemplatePath(trimmed)
    if (err) return err
  }
  return null
}
