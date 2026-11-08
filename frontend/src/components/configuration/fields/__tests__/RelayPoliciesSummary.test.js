import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RelayPoliciesSummary from '../RelayPoliciesSummary.vue'

const countOf = (value) => mount(RelayPoliciesSummary, { props: { value } }).find('strong').text()

describe('RelayPoliciesSummary', () => {
  it('sums policies across relays in the nested { relay_id: { target: policy } } shape', () => {
    expect(countOf({ r1: { C0AAAAAAAA: 'auto', C0BBBBBBBB: 'ask' }, r2: { X1: 'auto' } })).toBe('3')
  })
  it('counts a legacy flat entry as one and handles empty values', () => {
    expect(countOf({ C0AAAAAAAA: 'auto' })).toBe('1')
    expect(countOf(null)).toBe('0')
  })
})
