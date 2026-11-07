import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { useVirtualNavigation } from '@/composables/useVirtualNavigation'

// Issue #1748 (stage: offset-model): a minimal fake virtualizer — just enough of
// @tanstack/virtual-core's public surface (scrollToIndex, getVirtualItems, getTotalSize) for the
// composable to drive — so these tests exercise the composable's own control flow in isolation,
// independent of the real library or any DOM/Vue-component rendering.
function makeFakeVirtualizer({ mountedIndexes = [] } = {}) {
  return {
    scrollToIndex: vi.fn(),
    getTotalSize: vi.fn(() => 0),
    getVirtualItems: vi.fn(() => mountedIndexes.map(index => ({ index }))),
  }
}

describe('useVirtualNavigation', () => {
  it('returns false without calling scrollToIndex when the virtualizer is unavailable', async () => {
    const virtualizerRef = ref(null)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    const result = await scrollToItemIndex(3)

    expect(result).toBe(false)
  })

  it('returns false for a null/negative index without calling scrollToIndex', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [0, 1, 2] })
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    expect(await scrollToItemIndex(null)).toBe(false)
    expect(await scrollToItemIndex(-1)).toBe(false)
    expect(fake.scrollToIndex).not.toHaveBeenCalled()
  })

  it('calls scrollToIndex and resolves true once the target index is in the rendered range', async () => {
    // Stage 1 (overscan = full item count): the target is always immediately in range, so this
    // is the common case — the two-phase "estimate then confirm" pattern only matters once
    // Stage 2 introduces real culling.
    const fake = makeFakeVirtualizer({ mountedIndexes: [3, 4, 5] })
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    const result = await scrollToItemIndex(4, { align: 'center' })

    expect(fake.scrollToIndex).toHaveBeenCalledWith(4, { align: 'center', behavior: 'auto' })
    expect(result).toBe(true)
  })

  it('forces a fresh measurement pass (getTotalSize) before scrollToIndex to avoid a stale measurementsCache read', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [0] })
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    await scrollToItemIndex(0)

    expect(fake.getTotalSize).toHaveBeenCalled()
  })

  it('resolves false when the target index never appears in the rendered range within the retry budget', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [] }) // never mounts
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    const result = await scrollToItemIndex(10)

    expect(result).toBe(false)
    expect(fake.getVirtualItems).toHaveBeenCalled()
  })

  it('waitForIndexMounted returns false if the virtualizer disappears mid-poll (e.g. component unmounted)', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [] })
    const virtualizerRef = ref(fake)
    const { waitForIndexMounted } = useVirtualNavigation(virtualizerRef)

    const promise = waitForIndexMounted(0)
    virtualizerRef.value = null
    const result = await promise

    expect(result).toBe(false)
  })
})

// Issue #2342: jump motion. A fake virtualizer over a fake scroller; real rAF (jsdom) with the short
// tween/settle durations keeps these tests fast.
describe('useVirtualNavigation jump motion (issue #2342)', () => {
  const VIEWPORT = 800
  function makeMotionEnv({ targetOffset = 1600, total = 100000, startTop = 0 } = {}) {
    const el = document.createElement('div')
    Object.defineProperty(el, 'clientHeight', { value: VIEWPORT, configurable: true })
    el.scrollTop = startTop
    el.scrollTo = vi.fn(({ top }) => { el.scrollTop = top })
    const state = { target: targetOffset }
    const fake = {
      scrollElement: el,
      scrollToIndex: vi.fn(),
      getTotalSize: vi.fn(() => total),
      getOffsetForIndex: vi.fn(() => [state.target, 'start']),
      getVirtualItemForOffset: vi.fn((offset) => ({ index: Math.floor(offset / 100) })),
      getVirtualItems: vi.fn(() => [5, 16, 24, 100, 160, 400, 900].map(index => ({ index }))),
    }
    const calls = []
    const hooks = {
      onMotionStart: vi.fn(() => calls.push('start')),
      onMotionEnd: vi.fn(() => calls.push('end')),
      onFrame: vi.fn(),
      onJumpStart: vi.fn(),
      onLanded: vi.fn(() => calls.push('landed')),
      onUserCancel: vi.fn(),
      onCue: vi.fn(),
      describeJump: vi.fn((index, base) => ({ ...base, turns: 12 })),
    }
    return { el, fake, state, hooks, calls, nav: useVirtualNavigation(ref(fake), hooks) }
  }

  it('a short jump tweens, lands exactly with the exact path, holds the jump-open state and pulses once', async () => {
    const { el, fake, hooks, calls, nav } = makeMotionEnv({ targetOffset: 1600 })
    expect(await nav.scrollToItemIndex(16, { align: 'auto' })).toBe(true)
    expect(el.scrollTo.mock.calls.length).toBeGreaterThan(2) // animated, not a single write
    expect(el.scrollTop).toBe(1600)
    expect(fake.scrollToIndex).toHaveBeenCalledWith(16, { align: 'start', behavior: 'auto' }) // align resolved once
    expect(calls).toEqual(['start', 'landed', 'end'])
    expect(hooks.onLanded).toHaveBeenCalledWith(16)
    expect(hooks.onCue).not.toHaveBeenCalled() // cue is long jumps only
    expect(hooks.onFrame).toHaveBeenCalled()
  })

  it('a long jump first moves next to the target on the origin side, cues, then settles', async () => {
    const { el, hooks, nav } = makeMotionEnv({ targetOffset: 40000, startTop: 0 })
    await nav.scrollToItemIndex(400, { align: 'start' })
    expect(el.scrollTo.mock.calls[0][0].top).toBe(40000 - 1.5 * VIEWPORT)
    expect(el.scrollTop).toBe(40000)
    const cue = hooks.onCue.mock.calls[0][0]
    expect(cue.up).toBe(false)
    expect(cue.label).toBe('↓ 12 turns · 40%')
    expect(cue.toFrac).toBeCloseTo(40000 / (100000 - VIEWPORT))
    nav.cancelMotion()
    expect(hooks.onCue).toHaveBeenLastCalledWith(null)
  })

  it('describeJump can replace the cue content or suppress it', async () => {
    const env = makeMotionEnv({ targetOffset: 40000 })
    env.hooks.describeJump.mockReturnValue({ fromFrac: 0.9, toFrac: 0.1, up: true, turns: 1, pct: 80 })
    await env.nav.scrollToItemIndex(400, { align: 'start' })
    expect(env.hooks.onCue.mock.calls[0][0]).toEqual({ fromFrac: 0.9, toFrac: 0.1, up: true, label: '↑ 1 turn · 80%' })
    const none = makeMotionEnv({ targetOffset: 40000 })
    none.hooks.describeJump.mockReturnValue(null)
    await none.nav.scrollToItemIndex(400, { align: 'start' })
    expect(none.hooks.onCue).not.toHaveBeenCalled()
    expect(none.el.scrollTop).toBe(40000) // still animates and lands, only the cue is dropped
  })

  it('a long jump upward approaches from below the target', async () => {
    const { el, nav } = makeMotionEnv({ targetOffset: 10000, startTop: 90000 })
    await nav.scrollToItemIndex(100, { align: 'start' })
    expect(el.scrollTo.mock.calls[0][0].top).toBe(10000 + 1.5 * VIEWPORT)
    expect(el.scrollTop).toBe(10000)
  })

  it("motion: 'none' is the instant path: no hold, no cue, no pulse, original align", async () => {
    const { el, fake, hooks, nav } = makeMotionEnv({ targetOffset: 40000 })
    expect(await nav.scrollToItemIndex(400, { align: 'center', motion: 'none' })).toBe(true)
    expect(el.scrollTo).not.toHaveBeenCalled()
    expect(fake.scrollToIndex).toHaveBeenCalledWith(400, { align: 'center', behavior: 'auto' })
    for (const key of ['onMotionStart', 'onMotionEnd', 'onCue', 'onLanded', 'onJumpStart']) expect(hooks[key]).not.toHaveBeenCalled()
  })

  it('pulse: false lands without the pulse hook', async () => {
    const { hooks, nav } = makeMotionEnv({ targetOffset: 1600 })
    await nav.scrollToItemIndex(16, { align: 'start', pulse: false })
    expect(hooks.onLanded).not.toHaveBeenCalled()
  })

  it('reduced motion: one exact instant jump, no hold, no cue, the pulse hook still runs', async () => {
    const original = window.matchMedia
    window.matchMedia = vi.fn(() => ({ matches: true }))
    try {
      const { el, fake, hooks, nav } = makeMotionEnv({ targetOffset: 40000 })
      expect(await nav.scrollToItemIndex(400, { align: 'start' })).toBe(true)
      expect(el.scrollTo).not.toHaveBeenCalled()
      expect(fake.scrollToIndex).toHaveBeenCalledTimes(1)
      expect(hooks.onCue).not.toHaveBeenCalled()
      expect(hooks.onMotionStart).not.toHaveBeenCalled()
      expect(hooks.onLanded).toHaveBeenCalledWith(400)
    } finally {
      window.matchMedia = original
    }
  })

  it('a wheel mid-jump cancels: resolves false, no landing correction, no pulse, hold released, cue cleared', async () => {
    const { el, fake, hooks, nav } = makeMotionEnv({ targetOffset: 1600 })
    const promise = nav.scrollToItemIndex(16, { align: 'start' })
    await new Promise(resolve => setTimeout(resolve, 60))
    el.dispatchEvent(new Event('wheel'))
    expect(await promise).toBeNull()
    expect(fake.scrollToIndex).not.toHaveBeenCalled()
    expect(hooks.onLanded).not.toHaveBeenCalled()
    expect(hooks.onUserCancel).toHaveBeenCalledTimes(1)
    expect(hooks.onMotionEnd).toHaveBeenCalledTimes(1)
    const stoppedAt = el.scrollTop
    await new Promise(resolve => setTimeout(resolve, 80))
    expect(el.scrollTop).toBe(stoppedAt)
  })

  it('a newer jump supersedes: the old one resolves false and the hold stays balanced', async () => {
    const { hooks, nav, state } = makeMotionEnv({ targetOffset: 1600 })
    const first = nav.scrollToItemIndex(16, { align: 'start' })
    await new Promise(resolve => setTimeout(resolve, 40))
    state.target = 2400
    const second = nav.scrollToItemIndex(24, { align: 'start' })
    expect(await first).toBeNull()
    expect(await second).toBe(true)
    expect(hooks.onMotionStart).toHaveBeenCalledTimes(2)
    expect(hooks.onMotionEnd).toHaveBeenCalledTimes(2)
    expect(hooks.onLanded).toHaveBeenCalledTimes(1)
    expect(hooks.onUserCancel).not.toHaveBeenCalled()
  })

  it('cancelMotion (session switch / unmount) stops the animation without a landing', async () => {
    const { fake, hooks, nav } = makeMotionEnv({ targetOffset: 1600 })
    const promise = nav.scrollToItemIndex(16, { align: 'start' })
    await new Promise(resolve => setTimeout(resolve, 40))
    nav.cancelMotion('session')
    expect(await promise).toBeNull()
    expect(fake.scrollToIndex).not.toHaveBeenCalled()
    expect(hooks.onLanded).not.toHaveBeenCalled()
    expect(nav.isAnimating()).toBe(false)
  })

  it('a target that moves while the tween runs is followed and still lands exactly', async () => {
    const { el, state, nav } = makeMotionEnv({ targetOffset: 1600 })
    const promise = nav.scrollToItemIndex(16, { align: 'start' })
    await new Promise(resolve => setTimeout(resolve, 50))
    state.target = 1750 // rows near the target re-measured
    await promise
    expect(el.scrollTop).toBe(1750)
  })
})

describe('useVirtualNavigation cancel vs the virtualizer reconcile loop (issue #2342)', () => {
  it("a user cancel clears the virtualizer's own scrollToIndex reconcile state so nothing pulls the view back", async () => {
    const el = document.createElement('div')
    Object.defineProperty(el, 'clientHeight', { value: 800, configurable: true })
    el.scrollTo = vi.fn(({ top }) => { el.scrollTop = top })
    const fake = {
      scrollElement: el, scrollState: { index: 16 }, scrollToIndex: vi.fn(), getTotalSize: vi.fn(() => 100000),
      getOffsetForIndex: vi.fn(() => [1600, 'start']), getVirtualItemForOffset: vi.fn(() => ({ index: 1 })),
      getVirtualItems: vi.fn(() => [{ index: 16 }]),
    }
    const { scrollToItemIndex } = useVirtualNavigation(ref(fake))
    const promise = scrollToItemIndex(16, { align: 'start' })
    await new Promise(resolve => setTimeout(resolve, 40))
    el.dispatchEvent(new Event('touchstart'))
    expect(fake.scrollState).toBeNull()
    expect(await promise).toBeNull()
  })

  it('with fake timers: after a user cancel during the final landing the reconcile loop never pulls the view back', async () => {
    vi.useFakeTimers()
    try {
      const el = document.createElement('div')
      Object.defineProperty(el, 'clientHeight', { value: 800, configurable: true })
      el.scrollTo = vi.fn(({ top }) => { el.scrollTop = top })
      const fake = {
        scrollElement: el, scrollState: null, getTotalSize: vi.fn(() => 100000),
        getOffsetForIndex: vi.fn(() => [1600, 'start']), getVirtualItemForOffset: vi.fn(() => ({ index: 1 })),
        // never confirms the mount: the landing stays in its wait while the library's own loop keeps re-aiming
        getVirtualItems: vi.fn(() => []),
        scrollToIndex: vi.fn((index) => {
          fake.scrollState = { index }
          const timer = setInterval(() => {
            if (!fake.scrollState) { clearInterval(timer); return }
            el.scrollTo({ top: 1600 })
          }, 16)
        }),
      }
      const { scrollToItemIndex } = useVirtualNavigation(ref(fake))
      const promise = scrollToItemIndex(16, { align: 'start' })
      for (let i = 0; i < 100 && !fake.scrollToIndex.mock.calls.length; i++) await vi.advanceTimersByTimeAsync(16)
      expect(fake.scrollToIndex).toHaveBeenCalled() // the tween finished and the final exact landing started
      el.dispatchEvent(new Event('wheel')) // the user takes over ...
      el.scrollTop = 999 // ... and scrolls elsewhere
      await vi.advanceTimersByTimeAsync(600)
      expect(el.scrollTop).toBe(999) // nothing pulled the view back to the target
      expect(fake.scrollState).toBeNull()
      await vi.advanceTimersByTimeAsync(2000)
      expect(await promise).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('useVirtualNavigation review follow-ups (issue #2342)', () => {
  it('the virtualizer still exposes the private scrollState field stopLibraryReconcile clears (guards a library upgrade)', async () => {
    const { Virtualizer } = await import('@tanstack/virtual-core')
    const v = new Virtualizer({
      count: 1, getScrollElement: () => null, estimateSize: () => 10, scrollToFn() {},
      observeElementRect() {}, observeElementOffset() {},
    })
    expect('scrollState' in v).toBe(true)
  })

  it('a session switch while an instant jump awaits its mount drops its follow-ups (no pulse in the other session)', async () => {
    const hooks = { onLanded: vi.fn(), onJumpStart: vi.fn() }
    const fake = { scrollToIndex: vi.fn(), getTotalSize: () => 0, getVirtualItems: () => [{ index: 3 }] }
    const nav = useVirtualNavigation(ref(fake), hooks)
    const promise = nav.scrollToItemIndex(3, { align: 'start' })
    nav.cancelMotion('session')
    expect(await promise).toBeNull()
    expect(hooks.onLanded).not.toHaveBeenCalled()
  })

  it('a tween frame that throws releases the hold instead of leaving the caller waiting forever', async () => {
    const el = document.createElement('div')
    Object.defineProperty(el, 'clientHeight', { value: 800, configurable: true })
    el.scrollTo = vi.fn(() => { throw new Error('detached') })
    const hooks = { onMotionStart: vi.fn(), onMotionEnd: vi.fn(), onLanded: vi.fn() }
    const fake = {
      scrollElement: el, scrollToIndex: vi.fn(), getTotalSize: () => 100000, getOffsetForIndex: () => [1600, 'start'],
      getVirtualItemForOffset: () => ({ index: 1 }), getVirtualItems: () => [{ index: 16 }],
    }
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const nav = useVirtualNavigation(ref(fake), hooks)
    expect(await nav.scrollToItemIndex(16, { align: 'start' })).toBeNull()
    expect(hooks.onMotionEnd).toHaveBeenCalledTimes(1)
    expect(hooks.onLanded).not.toHaveBeenCalled()
    spy.mockRestore()
  })

  it('a replaced long-jump cue is cleared when a shorter jump starts', async () => {
    const el = document.createElement('div')
    Object.defineProperty(el, 'clientHeight', { value: 800, configurable: true })
    el.scrollTo = vi.fn(({ top }) => { el.scrollTop = top })
    let target = 40000
    const hooks = { onCue: vi.fn() }
    const fake = {
      scrollElement: el, scrollToIndex: vi.fn(), getTotalSize: () => 100000, getOffsetForIndex: () => [target, 'start'],
      getVirtualItemForOffset: () => ({ index: 1 }), getVirtualItems: () => [{ index: 5 }, { index: 400 }, { index: 20 }],
    }
    const nav = useVirtualNavigation(ref(fake), hooks)
    await nav.scrollToItemIndex(400, { align: 'start' })
    expect(hooks.onCue).toHaveBeenLastCalledWith(expect.objectContaining({ label: expect.any(String) }))
    target = el.scrollTop + 500 // a short jump
    await nav.scrollToItemIndex(20, { align: 'start' })
    expect(hooks.onCue).toHaveBeenLastCalledWith(null)
  })
})
