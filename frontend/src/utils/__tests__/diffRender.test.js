import { describe, it, expect } from 'vitest'
import { buildEditDiff, buildUnifiedDiffText } from '@/utils/diffRender'

const OLD = 'line one\nline two\nline three\n'
const NEW = 'line one\nline TWO\nline three\n'

describe('buildUnifiedDiffText', () => {
  it('returns header-stripped unified diff text starting with a @@ hunk line', () => {
    const text = buildUnifiedDiffText('file.txt', OLD, NEW)
    const lines = text.split('\n')
    expect(lines[0]).toMatch(/^@@/)
    expect(text).not.toContain('Index:')
    expect(text).not.toContain('===')
  })

  it('includes the added and removed lines', () => {
    const text = buildUnifiedDiffText('file.txt', OLD, NEW)
    expect(text).toContain('-line two')
    expect(text).toContain('+line TWO')
  })
})

describe('buildEditDiff', () => {
  it('reports matching added/removed counts post-refactor', () => {
    const { lines, added, removed } = buildEditDiff('file.txt', OLD, NEW)
    expect(added).toBe(1)
    expect(removed).toBe(1)
    expect(lines.some(l => l.type === 'added' && l.content === 'line TWO')).toBe(true)
    expect(lines.some(l => l.type === 'removed' && l.content === 'line two')).toBe(true)
    expect(lines.some(l => l.type === 'hunk')).toBe(true)
  })
})
