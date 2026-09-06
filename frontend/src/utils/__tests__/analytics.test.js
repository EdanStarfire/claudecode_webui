import { describe, it, expect } from 'vitest'
import { formatModelLabel } from '../analytics.js'

describe('formatModelLabel', () => {
  it('returns the generic placeholder for a null model', () => {
    expect(formatModelLabel(null, false)).toBe('(unknown)')
  })

  it('returns the generic placeholder for an empty model', () => {
    expect(formatModelLabel('', undefined)).toBe('(unknown)')
  })

  it('returns the raw model unchanged when rates are known', () => {
    expect(formatModelLabel('claude-sonnet-4-6', true)).toBe('claude-sonnet-4-6')
  })

  it('prefixes an unpriced-but-present model with "unknown "', () => {
    expect(formatModelLabel('future-model-xyz', false)).toBe('unknown future-model-xyz')
  })

  it('keeps two distinct unpriced models as two distinct labels', () => {
    const a = formatModelLabel('model-a', false)
    const b = formatModelLabel('model-b', false)
    expect(a).not.toBe(b)
    expect(a).toBe('unknown model-a')
    expect(b).toBe('unknown model-b')
  })
})
