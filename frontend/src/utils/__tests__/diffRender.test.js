import { describe, it, expect } from 'vitest'
import { buildEditDiff, buildUnifiedDiffText, resolveVersionDiffPair } from '@/utils/diffRender'

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

describe('resolveVersionDiffPair', () => {
  const makeResource = () => {
    const versions = [
      { resource_id: 'r3', version_number: 3 },
      { resource_id: 'r2', version_number: 2 },
      { resource_id: 'r1', version_number: 1 }
    ]
    return { original_filename: 'doc.md', version_count: 3, versions }
  }

  it('pairs a version against its previous sibling', () => {
    const resource = makeResource()
    const v2 = resource.versions.find(v => v.version_number === 2)
    const pair = resolveVersionDiffPair(resource, v2, 'previous')
    expect(pair.oldResourceId).toBe('r1')
    expect(pair.newResourceId).toBe('r2')
    expect(pair.oldVersionNumber).toBe(1)
    expect(pair.newVersionNumber).toBe(2)
  })

  it('returns null for diff-vs-previous on the oldest version', () => {
    const resource = makeResource()
    const v1 = resource.versions.find(v => v.version_number === 1)
    expect(resolveVersionDiffPair(resource, v1, 'previous')).toBeNull()
  })

  it('pairs a version against the latest sibling', () => {
    const resource = makeResource()
    const v1 = resource.versions.find(v => v.version_number === 1)
    const pair = resolveVersionDiffPair(resource, v1, 'latest')
    expect(pair.oldResourceId).toBe('r1')
    expect(pair.newResourceId).toBe('r3')
    expect(pair.oldVersionNumber).toBe(1)
    expect(pair.newVersionNumber).toBe(3)
  })

  it('returns null for diff-vs-latest on the latest version itself', () => {
    const resource = makeResource()
    const v3 = resource.versions.find(v => v.version_number === 3)
    expect(resolveVersionDiffPair(resource, v3, 'latest')).toBeNull()
  })

  it('always orders the label old→new regardless of which button was clicked', () => {
    const resource = makeResource()
    const v1 = resource.versions.find(v => v.version_number === 1)
    const v2 = resource.versions.find(v => v.version_number === 2)

    const viaLatest = resolveVersionDiffPair(resource, v1, 'latest')
    expect(viaLatest.oldVersionNumber).toBeLessThan(viaLatest.newVersionNumber)
    expect(viaLatest.label).toBe('doc.md — v1 → v3')

    const viaPrevious = resolveVersionDiffPair(resource, v2, 'previous')
    expect(viaPrevious.oldVersionNumber).toBeLessThan(viaPrevious.newVersionNumber)
    expect(viaPrevious.label).toBe('doc.md — v1 → v2')
  })
})
