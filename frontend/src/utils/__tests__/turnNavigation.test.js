import { describe, it, expect } from 'vitest'
import {
  cursorOffset, findTurnBefore, estimateJumpWireBytes, jumpNeedsConfirm, formatJumpSize, jumpProgress,
  JUMP_CONFIRM_WIRE_BYTES, DEFAULT_WIRE_RATIO,
} from '../turnNavigation'

// Issue #2030 (stage 2 PR B): pure helpers behind turn navigation over the server history index.

const turns = [['b:10', 'a', 10], ['b:50', 'b', 50], ['b:90', 'c', 90], ['b:400', 'd', 400]]

describe('cursorOffset', () => {
  it('parses b:<offset> cursors only', () => {
    expect(cursorOffset('b:123')).toBe(123)
    expect(cursorOffset('b:0')).toBe(0)
    expect(cursorOffset('opaque-xyz')).toBeNull()
    expect(cursorOffset('b:1x')).toBeNull()
    expect(cursorOffset(undefined)).toBeNull()
  })
})

describe('findTurnBefore', () => {
  it('returns the newest turn strictly before the loaded start, with its ordinal position', () => {
    expect(findTurnBefore(turns, 400)).toEqual({ position: 2, cursor: 'b:90', messageId: 'c', sizeHint: 90 })
    expect(findTurnBefore(turns, 401)).toMatchObject({ position: 3, messageId: 'd' })
    expect(findTurnBefore(turns, 51)).toMatchObject({ position: 1, messageId: 'b' })
  })

  it('returns null when nothing precedes the loaded start (or the index is empty)', () => {
    expect(findTurnBefore(turns, 10)).toBeNull()
    expect(findTurnBefore(turns, 0)).toBeNull()
    expect(findTurnBefore([], 100)).toBeNull()
  })

  it('agrees with a linear scan for every start offset', () => {
    for (let start = 0; start <= 450; start += 7) {
      const linear = [...turns].reverse().find(t => t[2] < start)
      expect(findTurnBefore(turns, start)?.messageId ?? null).toBe(linear?.[1] ?? null)
    }
  })
})

describe('jump size guard', () => {
  it('estimates wire bytes as the byte distance over the observed ratio (default 5x)', () => {
    expect(estimateJumpWireBytes(10_000_000, 0, 5)).toBe(2_000_000)
    expect(estimateJumpWireBytes(10_000_000, 0, null)).toBe(10_000_000 / DEFAULT_WIRE_RATIO)
    expect(estimateJumpWireBytes(100, 500, 5)).toBe(0)
  })

  it('always confirms on a slow link; otherwise only above the cap', () => {
    expect(jumpNeedsConfirm({ slowLink: true, estimatedWireBytes: 1 })).toBe(true)
    expect(jumpNeedsConfirm({ slowLink: false, estimatedWireBytes: JUMP_CONFIRM_WIRE_BYTES })).toBe(false)
    expect(jumpNeedsConfirm({ slowLink: false, estimatedWireBytes: JUMP_CONFIRM_WIRE_BYTES + 1 })).toBe(true)
  })

  it('formats sizes for the confirm text', () => {
    expect(formatJumpSize(3.4 * 1024 * 1024)).toBe('3.4 MB')
    expect(formatJumpSize(20 * 1024)).toBe('20 KB')
    expect(formatJumpSize(10)).toBe('1 KB')
  })
})

describe('jumpProgress', () => {
  const jump = { fromOffset: 1000, toOffset: 200 }
  it('is the covered share of the jump distance, clamped to 0..1', () => {
    expect(jumpProgress('b:1000', jump)).toBe(0)
    expect(jumpProgress('b:600', jump)).toBe(0.5)
    expect(jumpProgress('b:100', jump)).toBe(1)
  })
  it('is null without a usable cursor or distance', () => {
    expect(jumpProgress('opaque', jump)).toBeNull()
    expect(jumpProgress('b:5', { fromOffset: 10, toOffset: 10 })).toBeNull()
    expect(jumpProgress('b:5', null)).toBeNull()
  })
})
