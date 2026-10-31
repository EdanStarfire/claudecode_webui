import { describe, it, expect } from 'vitest'
import { MAX_PREFETCH_VIEWPORTS, PREFETCH_VIEWPORTS, nextScrollSpeed, prefetchDistancePx } from '@/utils/scrollPrefetch'

describe('prefetchDistancePx (issue #2030 stage 3 PR 3D)', () => {
  it('is two viewport heights when idle or slow', () => {
    expect(prefetchDistancePx(800, 0, 0)).toBe(PREFETCH_VIEWPORTS * 800)
    expect(prefetchDistancePx(800, 0.5, 100)).toBe(PREFETCH_VIEWPORTS * 800)
  })

  it('widens to what is scrolled through during one page latency', () => {
    // 5 px/ms for 600 ms with a 1.5 margin + one screen = 4500 + 800
    expect(prefetchDistancePx(800, 5, 600)).toBe(5300)
  })

  it('is capped at MAX_PREFETCH_VIEWPORTS viewports', () => {
    expect(prefetchDistancePx(800, 100, 5000)).toBe(MAX_PREFETCH_VIEWPORTS * 800)
  })

  it('ignores movement away from the edge and unknown latency', () => {
    expect(prefetchDistancePx(800, -3, 600)).toBe(PREFETCH_VIEWPORTS * 800)
    expect(prefetchDistancePx(800, 3, undefined)).toBe(PREFETCH_VIEWPORTS * 800)
  })
})

describe('nextScrollSpeed', () => {
  it('smooths the instantaneous speed and keeps its sign', () => {
    expect(nextScrollSpeed(0, 1000, 100, 900, 150)).toBeCloseTo(-1) // 0.5 * (-2 px/ms)
    expect(nextScrollSpeed(-1, 900, 150, 800, 200)).toBeCloseTo(-1.5)
  })

  it('caps a single-frame rewrite of scrollTop (anchored prepend / jump) at the maximum plausible speed', () => {
    expect(nextScrollSpeed(0, 0, 100, 12000, 116)).toBeCloseTo(10) // 0.5 * 20 px/ms, not 0.5 * 750
    expect(nextScrollSpeed(0, 12000, 100, 0, 116)).toBeCloseTo(-10)
  })

  it('resets without a previous sample or after a pause', () => {
    expect(nextScrollSpeed(3, 0, 0, 100, 50)).toBe(0)
    expect(nextScrollSpeed(3, 100, 10, 0, 700)).toBe(0)
  })
})
