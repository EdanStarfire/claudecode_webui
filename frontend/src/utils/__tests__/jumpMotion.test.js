import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  APPROACH_VIEWPORTS, SETTLE_MS, SHORT_MAX_MS, SHORT_MAX_VIEWPORTS,
  approachOffset, createJumpMotion, easeInOutCubic, easeOutCubic, planJump, prefersReducedMotion, shortDuration,
} from '@/utils/jumpMotion'

// Issue #2342: fake clock + fake rAF so the tween runs deterministically.
function makeClock() {
  let t = 0
  let nextId = 1
  const queue = new Map()
  return {
    now: () => t,
    raf: (cb) => { const id = nextId++; queue.set(id, cb); return id },
    caf: (id) => queue.delete(id),
    pending: () => queue.size,
    tick(ms = 16) {
      t += ms
      const cbs = [...queue.values()]
      queue.clear()
      cbs.forEach(cb => cb(t))
    },
    run(ms) { for (let elapsed = 0; elapsed < ms; elapsed += 16) this.tick(16) },
  }
}

function makeEl() {
  const listeners = new Map()
  return {
    scrollTop: 0,
    clientHeight: 800,
    ownerDocument: { activeElement: null, defaultView: null },
    scrollTo: vi.fn(function ({ top }) { this.scrollTop = top }),
    addEventListener: vi.fn((type, fn) => { listeners.set(type, (listeners.get(type) || new Set()).add(fn)) }),
    removeEventListener: vi.fn((type, fn) => { listeners.get(type)?.delete(fn) }),
    listenerCount: () => [...listeners.values()].reduce((n, set) => n + set.size, 0),
    emit(type, ev = {}) { listeners.get(type)?.forEach(fn => fn(ev)) },
  }
}

describe('planJump', () => {
  it('classifies at the threshold: <= 3 viewports is short, beyond is long', () => {
    expect(planJump({ current: 0, target: 800 * SHORT_MAX_VIEWPORTS, viewport: 800 }).kind).toBe('short')
    expect(planJump({ current: 0, target: 800 * SHORT_MAX_VIEWPORTS + 1, viewport: 800 }).kind).toBe('long')
  })

  it('is instant for no movement and for an unmeasured viewport', () => {
    expect(planJump({ current: 100, target: 101, viewport: 800 }).kind).toBe('instant')
    expect(planJump({ current: 0, target: 5000, viewport: 0 }).kind).toBe('instant')
  })

  it('reports direction', () => {
    expect(planJump({ current: 5000, target: 4000, viewport: 800 }).up).toBe(true)
    expect(planJump({ current: 0, target: 4000, viewport: 800 }).up).toBe(false)
  })

  it('short duration is 140 + 70/viewport capped at 350 ms; long is the settle', () => {
    expect(shortDuration(1)).toBe(210)
    expect(shortDuration(3)).toBe(SHORT_MAX_MS)
    expect(shortDuration(100)).toBe(SHORT_MAX_MS)
    expect(planJump({ current: 0, target: 80000, viewport: 800 }).duration).toBe(SETTLE_MS)
  })
})

describe('approachOffset', () => {
  it('starts 1.5 viewports before the target on the side the user came from', () => {
    expect(approachOffset({ target: 10000, up: false, viewport: 800, max: 50000 })).toBe(10000 - APPROACH_VIEWPORTS * 800)
    expect(approachOffset({ target: 10000, up: true, viewport: 800, max: 50000 })).toBe(10000 + APPROACH_VIEWPORTS * 800)
  })

  it('clamps to [0, max]', () => {
    expect(approachOffset({ target: 100, up: false, viewport: 800, max: 50000 })).toBe(0)
    expect(approachOffset({ target: 49900, up: true, viewport: 800, max: 50000 })).toBe(50000)
  })
})

describe('easing', () => {
  it('hits 0 and 1 and is monotonic', () => {
    for (const ease of [easeOutCubic, easeInOutCubic]) {
      expect(ease(0)).toBe(0)
      expect(ease(1)).toBe(1)
      let prev = -1
      for (let p = 0; p <= 1; p += 0.05) { expect(ease(p)).toBeGreaterThanOrEqual(prev); prev = ease(p) }
    }
  })
})

describe('prefersReducedMotion', () => {
  it('reads the media query at call time', () => {
    expect(prefersReducedMotion(() => ({ matches: true }))).toBe(true)
    expect(prefersReducedMotion(() => ({ matches: false }))).toBe(false)
    expect(prefersReducedMotion(null)).toBe(false)
  })
})

describe('createJumpMotion', () => {
  let clock
  let motion
  beforeEach(() => {
    clock = makeClock()
    motion = createJumpMotion(clock)
  })

  it('tweens to the target, ends exactly on it, and leaves no listeners or frames behind', async () => {
    const el = makeEl()
    const handle = motion.begin(el)
    const promise = handle.tween({ from: 0, resolveTarget: () => 1000, duration: 200, ease: easeInOutCubic })
    clock.run(300)
    expect(await promise).toBe('done')
    expect(el.scrollTop).toBe(1000)
    handle.end()
    expect(el.listenerCount()).toBe(0)
    expect(clock.pending()).toBe(0)
    expect(motion.isActive()).toBe(false)
  })

  it('finishes within the duration plus one frame for any distance', async () => {
    const el = makeEl()
    const handle = motion.begin(el)
    const promise = handle.tween({ from: 0, resolveTarget: () => 9e6, duration: SETTLE_MS, ease: easeOutCubic })
    let frames = 0
    let done = false
    promise.then(() => { done = true })
    while (!done && frames < 100) { clock.tick(16); frames++; await Promise.resolve() }
    expect(frames * 16).toBeLessThanOrEqual(SETTLE_MS + 16)
  })

  it('re-resolves the target every frame', async () => {
    const el = makeEl()
    const handle = motion.begin(el)
    let target = 1000
    const promise = handle.tween({ from: 0, resolveTarget: () => target, duration: 160, ease: (p) => p })
    clock.tick(16)
    target = 1400 // a row re-measured: the target moved mid-tween
    clock.run(300)
    await promise
    expect(el.scrollTop).toBe(1400)
  })

  it.each([
    ['wheel', (el) => el.emit('wheel')],
    ['touchstart', (el) => el.emit('touchstart')],
    ['mousedown', (el) => el.emit('mousedown')],
  ])('%s cancels where it stands, no further writes, reason user', async (_name, fire) => {
    const el = makeEl()
    const handle = motion.begin(el)
    const promise = handle.tween({ from: 0, resolveTarget: () => 1000, duration: 400, ease: (p) => p })
    clock.run(100)
    const at = el.scrollTop
    fire(el)
    expect(await promise).toBe('cancelled')
    expect(handle.reason).toBe('user')
    const writes = el.scrollTo.mock.calls.length
    clock.run(500)
    expect(el.scrollTo.mock.calls.length).toBe(writes)
    expect(el.scrollTop).toBe(at)
    expect(el.listenerCount()).toBe(0)
  })

  it('a scroll key on the window cancels, other keys and text entry do not', async () => {
    const win = makeEl()
    const el = makeEl()
    el.ownerDocument = { activeElement: null, defaultView: win }
    const handle = motion.begin(el)
    const promise = handle.tween({ from: 0, resolveTarget: () => 1000, duration: 400, ease: (p) => p })
    win.emit('keydown', { key: 'a' })
    expect(handle.cancelled).toBe(false)
    el.ownerDocument.activeElement = { tagName: 'TEXTAREA' }
    win.emit('keydown', { key: 'ArrowDown' })
    expect(handle.cancelled).toBe(false)
    el.ownerDocument.activeElement = null
    win.emit('keydown', { key: 'PageDown' })
    expect(await promise).toBe('cancelled')
    expect(win.listenerCount()).toBe(0)
  })

  it('a new jump supersedes the running one', async () => {
    const el = makeEl()
    const first = motion.begin(el)
    const firstPromise = first.tween({ from: 0, resolveTarget: () => 1000, duration: 400, ease: (p) => p })
    clock.run(48)
    const second = motion.begin(el)
    expect(await firstPromise).toBe('cancelled')
    expect(first.reason).toBe('superseded')
    expect(second.cancelled).toBe(false)
    expect(el.listenerCount()).toBe(3) // only the second handle's wheel/touchstart/mousedown (keydown is on the window)
  })

  it('cancel() before the first frame resolves cancelled and a cancelled handle tweens nothing', async () => {
    const el = makeEl()
    const handle = motion.begin(el)
    handle.cancel('session')
    expect(await handle.tween({ from: 0, resolveTarget: () => 5, duration: 100, ease: (p) => p })).toBe('cancelled')
    expect(el.scrollTo).not.toHaveBeenCalled()
  })

  it('calls onFrame every frame', async () => {
    const el = makeEl()
    const handle = motion.begin(el)
    const onFrame = vi.fn()
    const promise = handle.tween({ from: 0, resolveTarget: () => 100, duration: 64, ease: (p) => p, onFrame })
    clock.run(100)
    await promise
    expect(onFrame.mock.calls.length).toBeGreaterThanOrEqual(4)
  })
})
