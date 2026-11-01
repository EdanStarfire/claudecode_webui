import { describe, it, expect } from 'vitest'
import {
  windowBytes, intervalsTouch, gapBetween, formatHistorySize,
} from '../historyIntervals'

// Issue #2030 (stage 3 PR 3C-1): pure helpers of the interval model.

describe('windowBytes', () => {
  it('is the file bytes between two offset cursors; null for opaque cursors', () => {
    expect(windowBytes('b:100', 'b:612')).toBe(512)
    expect(windowBytes('b:100', 'b:50')).toBe(0)
    expect(windowBytes('row:1', 'b:5')).toBeNull()
    expect(windowBytes(undefined, 'b:5')).toBeNull()
  })
})

describe('intervalsTouch', () => {
  const suffix = { startCursor: 'b:1000' }
  it('touches when the island end reaches the suffix start (or the read ran to the file end)', () => {
    expect(intervalsTouch({ endCursor: 'b:1000', hasMoreAfter: true }, suffix)).toBe(true)
    expect(intervalsTouch({ endCursor: 'b:1500', hasMoreAfter: true }, suffix)).toBe(true)
    expect(intervalsTouch({ endCursor: 'b:200', hasMoreAfter: false }, suffix)).toBe(true)
  })
  it('does not touch while a gap remains', () => {
    expect(intervalsTouch({ endCursor: 'b:999', hasMoreAfter: true }, suffix)).toBe(false)
  })
  it('opaque cursors rely on record overlap', () => {
    expect(intervalsTouch({ endCursor: 'x:1', hasMoreAfter: true }, { startCursor: 'x:9' })).toBe(false)
    expect(intervalsTouch({ endCursor: 'x:1', hasMoreAfter: true }, { startCursor: 'x:9' }, true)).toBe(true)
  })
  it('is false without both intervals', () => {
    expect(intervalsTouch(null, suffix)).toBe(false)
    expect(intervalsTouch({ endCursor: 'b:1', hasMoreAfter: true }, null)).toBe(false)
  })
})

describe('gap and sizes', () => {
  it('gapBetween reports the unloaded stretch', () => {
    expect(gapBetween({ endCursor: 'b:100' }, { startCursor: 'b:5100' })).toEqual({ fromCursor: 'b:100', toCursor: 'b:5100', approxBytes: 5000 })
    expect(gapBetween({ endCursor: 'x' }, { startCursor: 'y' })).toEqual({ fromCursor: 'x', toCursor: 'y', approxBytes: null })
    expect(gapBetween(null, { startCursor: 'b:1' })).toBeNull()
  })
  it('formats sizes', () => {
    expect(formatHistorySize(null)).toBeNull()
    expect(formatHistorySize(20 * 1024)).toBe('20 KB')
    expect(formatHistorySize(512 * 1024)).toBe('0.5 MB')
    expect(formatHistorySize(19 * 1024 * 1024)).toBe('19 MB')
    expect(formatHistorySize(3.4 * 1024 * 1024)).toBe('3.4 MB')
  })
})
