import { describe, it, expect } from 'vitest'
import { assignGutterSlots } from '@/utils/subagentGutterLayout'

describe('assignGutterSlots (#1746 follow-up: historical + live lanes stack correctly)', () => {
  it('non-overlapping lanes reuse the same slot', () => {
    const lanes = [
      { id: 'a', top: 0, bottom: 100 },
      { id: 'b', top: 150, bottom: 250 }, // starts after a ends — no conflict
    ]
    const assignment = assignGutterSlots(lanes)
    expect(assignment.get('a')).toBe(0)
    expect(assignment.get('b')).toBe(0)
  })

  it('overlapping lanes get distinct slots, ordered by which appears first (top)', () => {
    const lanes = [
      { id: 'a', top: 0, bottom: 100 },
      { id: 'b', top: 50, bottom: 150 }, // overlaps a
    ]
    const assignment = assignGutterSlots(lanes)
    expect(assignment.get('a')).toBe(0)
    expect(assignment.get('b')).toBe(1)
  })

  it('is unbounded — 3+ concurrent lanes each get their own slot, no cap', () => {
    const lanes = [
      { id: 'a', top: 0, bottom: 300 },
      { id: 'b', top: 10, bottom: 300 },
      { id: 'c', top: 20, bottom: 300 },
      { id: 'd', top: 30, bottom: 300 },
    ]
    const assignment = assignGutterSlots(lanes)
    expect(new Set(assignment.values()).size).toBe(4)
    expect(assignment.get('a')).toBe(0)
    expect(assignment.get('d')).toBe(3)
  })

  it('reuses a freed slot once its occupant ends, even for a later-launched, still-later-ending lane', () => {
    const lanes = [
      { id: 'a', top: 0, bottom: 50 },     // ends early
      { id: 'b', top: 10, bottom: 200 },   // overlaps a — different slot
      { id: 'c', top: 60, bottom: 90 },    // starts after a ends — reuses a's slot (0), not a new one
    ]
    const assignment = assignGutterSlots(lanes)
    expect(assignment.get('a')).toBe(0)
    expect(assignment.get('b')).toBe(1)
    expect(assignment.get('c')).toBe(0)
  })

  it('handles a mix of long-retired and currently-active (open-ended) lanes without collapsing them onto the same slot', () => {
    // Mirrors the real scenario: two historical legs that happened to overlap long ago, plus a
    // leg still running now (bottom = Infinity-ish, i.e. far past everything else).
    const lanes = [
      { id: 'retired-1', top: 0, bottom: 100 },
      { id: 'retired-2', top: 50, bottom: 150 }, // overlaps retired-1
      { id: 'still-running', top: 500, bottom: 999999 }, // no overlap with either retired lane
    ]
    const assignment = assignGutterSlots(lanes)
    expect(assignment.get('retired-1')).toBe(0)
    expect(assignment.get('retired-2')).toBe(1)
    expect(assignment.get('still-running')).toBe(0) // safely reuses slot 0 — no temporal overlap
  })

  it('returns an empty map for no lanes', () => {
    expect(assignGutterSlots([]).size).toBe(0)
  })
})
