import { describe, it, expect } from 'vitest'
import { preprocessLeadingThematicBreak } from '../markdownPreprocess'

describe('preprocessLeadingThematicBreak', () => {
  it('fixes bug case: leading ---\\n with no closing delimiter', () => {
    expect(preprocessLeadingThematicBreak('---\n\n## Hi')).toBe('* * *\n\n## Hi')
  })

  it('leaves genuine frontmatter alone: closing \\n---\\n', () => {
    const input = '---\ntitle: x\n---\n# Body'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves genuine frontmatter alone: closing \\n--- at EOF', () => {
    const input = '---\ntitle: x\n---'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves content unchanged when --- is not on line 0', () => {
    const input = '# H\n\n---\nrest'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves plain text unchanged', () => {
    const input = 'Hello world'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves empty string unchanged', () => {
    expect(preprocessLeadingThematicBreak('')).toBe('')
  })

  it('returns non-string values as-is', () => {
    expect(preprocessLeadingThematicBreak(null)).toBe(null)
    expect(preprocessLeadingThematicBreak(undefined)).toBe(undefined)
    expect(preprocessLeadingThematicBreak(42)).toBe(42)
  })

  it('leaves --- without trailing newline unchanged', () => {
    expect(preprocessLeadingThematicBreak('---')).toBe('---')
  })

  it('fixes leading ---\\n followed by mid-line --- (not on own line)', () => {
    expect(preprocessLeadingThematicBreak('---\nfoo---bar')).toBe('* * *\nfoo---bar')
  })
})
