import { describe, it, expect } from 'vitest'
import {
  cursorOffset, findTurnBefore, findTurnAtOrAfter, jumpNeedsConfirm, formatJumpSize, planTurnStep,
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

describe('findTurnAtOrAfter', () => {
  it('returns the first turn starting at or after the offset', () => {
    expect(findTurnAtOrAfter(turns, 0)).toMatchObject({ position: 0, messageId: 'a' })
    expect(findTurnAtOrAfter(turns, 50)).toMatchObject({ position: 1, messageId: 'b' })
    expect(findTurnAtOrAfter(turns, 51)).toMatchObject({ position: 2, messageId: 'c' })
    expect(findTurnAtOrAfter(turns, 401)).toBeNull()
    expect(findTurnAtOrAfter([], 5)).toBeNull()
  })

  it('agrees with a linear scan and partitions the index with findTurnBefore', () => {
    for (let at = 0; at <= 450; at += 7) {
      const linear = turns.find(t => t[2] >= at)
      expect(findTurnAtOrAfter(turns, at)?.messageId ?? null).toBe(linear?.[1] ?? null)
      const before = findTurnBefore(turns, at)
      const after = findTurnAtOrAfter(turns, at)
      if (before && after) expect(after.position).toBe(before.position + 1)
    }
  })
})

describe('jump confirm', () => {
  it('only a slow link asks (one bounded window is the whole cost)', () => {
    expect(jumpNeedsConfirm({ slowLink: true })).toBe(true)
    expect(jumpNeedsConfirm({ slowLink: false })).toBe(false)
  })

  it('formats sizes for the confirm text', () => {
    expect(formatJumpSize(3.4 * 1024 * 1024)).toBe('3.4 MB')
    expect(formatJumpSize(512 * 1024)).toBe('0.5 MB')
    expect(formatJumpSize(20 * 1024)).toBe('20 KB')
    expect(formatJumpSize(10)).toBe('1 KB')
  })
})

describe('planTurnStep over loaded intervals + the index', () => {
  // index turns: t0..t9 at offsets 0,100,...,900. Suffix = turns 7..9 (starts at 700). Island = turns 2..4 ([200, 500)).
  const idx = Array.from({ length: 10 }, (_, i) => [`b:${i * 100}`, `t${i}`, i * 100])
  const loaded = new Set()
  const base = (over) => ({
    boundaries: [], gapIndex: -1, top: 0, bottom: 0, turns: idx, indexStatus: 'ready', complete: false,
    suffixStartOffset: 700, islandStartOffset: null, islandEndOffset: null,
    rowOfTurn: (id) => (loaded.has(id) ? 100 + Number(id.slice(1)) : null), ...over,
  })

  it('no island: nearest loaded boundary above/below; otherwise the index turn before the suffix (unloaded)', () => {
    loaded.clear(); ['t7', 't8', 't9'].forEach(id => loaded.add(id))
    const s = base({ boundaries: [1, 4, 7], top: 5, bottom: 6 })
    expect(planTurnStep('prev', s)).toEqual({ kind: 'loaded', index: 4 })
    expect(planTurnStep('next', s)).toEqual({ kind: 'loaded', index: 7 })
    const atTop = base({ boundaries: [1, 4, 7], top: 0, bottom: 0 })
    expect(planTurnStep('prev', atTop)).toMatchObject({ kind: 'unloaded', messageId: 't6', ordinal: 7, cursor: 'b:600' })
    expect(planTurnStep('next', base({ boundaries: [1], top: 3, bottom: 4 }))).toBeNull() // end of the loaded suffix
  })

  it('pending while the index is not ready; null when complete / nothing earlier', () => {
    const noIndex = base({ turns: null, indexStatus: 'loading', top: 0 })
    expect(planTurnStep('prev', noIndex)).toEqual({ kind: 'pending', reason: 'loading' })
    expect(planTurnStep('prev', { ...noIndex, indexStatus: null })).toEqual({ kind: 'pending', reason: 'idle' })
    expect(planTurnStep('prev', base({ complete: true, turns: null }))).toBeNull()
    expect(planTurnStep('prev', base({ suffixStartOffset: 0 }))).toBeNull()
    expect(planTurnStep('prev', base({ suffixStartOffset: null }))).toEqual({ kind: 'pending', reason: 'unavailable' })
  })

  describe('island above the suffix (gap row between)', () => {
    // rows: 0..5 island (boundaries 1,3), 6 gap, 7.. suffix (boundaries 8, 10)
    const rows = { gapIndex: 6, boundaries: [1, 3, 8, 10], islandStartOffset: 200, islandEndOffset: 500 }
    const setLoaded = () => { loaded.clear(); ['t2', 't3', 't4', 't7', 't8', 't9'].forEach(id => loaded.add(id)) }

    it('inside the suffix, Prev takes a suffix boundary above the viewport, not the island\'s', () => {
      setLoaded()
      expect(planTurnStep('prev', base({ ...rows, top: 9, bottom: 11 }))).toEqual({ kind: 'loaded', index: 8 })
    })

    it('at the top of the suffix, Prev goes to the turn right before it: unloaded in the gap', () => {
      setLoaded()
      const step = planTurnStep('prev', base({ ...rows, top: 7, bottom: 8 }))
      expect(step).toMatchObject({ kind: 'unloaded', messageId: 't6', cursor: 'b:600' })
    })

    it('...or the island\'s last turn (loaded, scroll) when the gap holds none', () => {
      setLoaded(); loaded.add('t4'); loaded.add('t5'); loaded.add('t6')
      const step = planTurnStep('prev', base({ ...rows, top: 7, bottom: 8, suffixStartOffset: 700 }))
      expect(step).toEqual({ kind: 'loaded', index: 106 })
    })

    it('inside the island, Next takes an island boundary below the viewport; past the last one it goes to the first turn after the island end', () => {
      setLoaded()
      expect(planTurnStep('next', base({ ...rows, top: 0, bottom: 2 }))).toEqual({ kind: 'loaded', index: 3 })
      const step = planTurnStep('next', base({ ...rows, top: 4, bottom: 5 }))
      expect(step).toMatchObject({ kind: 'unloaded', messageId: 't5', cursor: 'b:500' })
    })

    it('at the top of the island Prev uses the index before the island start; Next in the suffix has nothing past the live end', () => {
      setLoaded()
      expect(planTurnStep('prev', base({ ...rows, top: 0, bottom: 0 }))).toMatchObject({ kind: 'unloaded', messageId: 't1' })
      expect(planTurnStep('next', base({ ...rows, top: 9, bottom: 12 }))).toBeNull()
    })

    it('across a gap the rows on screen decide the interval, not the mounted (overscan) range', () => {
      setLoaded()
      // mounted rows 0..14 (overscan reaches back into the island) but the viewport shows suffix rows 8-10
      const s = base({ ...rows, top: 0, bottom: 14, viewTop: 8, viewBottom: 10 })
      expect(planTurnStep('prev', s)).toMatchObject({ kind: 'unloaded', messageId: 't6' }) // the turn before the suffix, not an island turn
      const inside = base({ ...rows, top: 0, bottom: 14, viewTop: 9, viewBottom: 11 })
      expect(planTurnStep('prev', inside)).toEqual({ kind: 'loaded', index: 8 })
      expect(planTurnStep('next', base({ ...rows, top: 0, bottom: 14, viewTop: 0, viewBottom: 5 }))).toMatchObject({ kind: 'unloaded', messageId: 't5' })
    })

    it('the gap row at the very top of the screen counts as the suffix for Prev', () => {
      setLoaded()
      expect(planTurnStep('prev', base({ ...rows, top: 6, bottom: 9, viewTop: 6, viewBottom: 9 }))).toMatchObject({ kind: 'unloaded', messageId: 't6' })
    })

    it('an island without offset cursors never falls back to the suffix start (that would skip the whole gap)', () => {
      setLoaded()
      expect(planTurnStep('prev', base({ ...rows, islandStartOffset: null, top: 0, bottom: 0 }))).toEqual({ kind: 'pending', reason: 'unavailable' })
    })

    it('is pending without an index (never guesses across the gap)', () => {
      setLoaded()
      expect(planTurnStep('next', base({ ...rows, turns: null, indexStatus: 'loading', top: 4, bottom: 5 }))).toEqual({ kind: 'pending', reason: 'loading' })
    })
  })
})
