import { describe, it, expect } from 'vitest'
import { normalizePolicies } from '@/utils/relayPolicies'

describe('relayPolicies', () => {
  it('keeps nested, wraps flat under the sole relay, drops flat when ambiguous', () => {
    expect(normalizePolicies({ r1: { C1: 'a' } }, 'r1')).toEqual({ r1: { C1: 'a' } })
    expect(normalizePolicies({ C1: 'a' }, 'r1')).toEqual({ r1: { C1: 'a' } })
    expect(normalizePolicies({ C1: 'a' }, null)).toEqual({})
    expect(normalizePolicies(null, 'r1')).toEqual({})
    expect(normalizePolicies('junk', 'r1')).toEqual({})
  })
})
