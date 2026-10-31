import { describe, it, expect, vi } from 'vitest'
import { scrollWithSizerGuard, shouldAdjustForItemSizeChange } from '@/utils/virtualScroll'

function makeEl({ scrollHeight = 1000, clientHeight = 400 } = {}) {
  return { scrollHeight, clientHeight, scrollTo: vi.fn() }
}
function makeDefer() {
  const queue = []
  return { defer: (fn) => queue.push(fn), flush: () => queue.splice(0).forEach(fn => fn()), queue }
}

describe('scrollWithSizerGuard (issue #2320)', () => {
  it('writes immediately when the target is inside the reachable range', () => {
    const el = makeEl(); const { defer, queue } = makeDefer()
    scrollWithSizerGuard(500, { adjustments: 20, behavior: 'auto' }, { scrollElement: el }, defer)
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 520, behavior: 'auto' })
    expect(queue).toHaveLength(0)
  })

  it('writes the exact maximum immediately', () => {
    const el = makeEl(); const { defer } = makeDefer()
    scrollWithSizerGuard(600, {}, { scrollElement: el }, defer)
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 600, behavior: undefined })
  })

  it('defers a write past the reachable range until the DOM has caught up', () => {
    const el = makeEl(); const { defer, flush } = makeDefer()
    scrollWithSizerGuard(3800, { behavior: 'auto' }, { scrollElement: el }, defer)
    expect(el.scrollTo).not.toHaveBeenCalled()
    el.scrollHeight = 4200 // the spacer grew
    flush()
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 3800, behavior: 'auto' })
  })

  it('keeps waiting while the DOM has not grown yet, and gives up after a few turns', () => {
    const el = makeEl(); const { defer, flush, queue } = makeDefer()
    scrollWithSizerGuard(3800, {}, { scrollElement: el }, defer)
    flush(); flush()
    expect(el.scrollTo).not.toHaveBeenCalled()
    el.scrollHeight = 4200
    flush()
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    expect(queue).toHaveLength(0)

    const stuck = makeEl(); const d2 = makeDefer()
    scrollWithSizerGuard(3800, {}, { scrollElement: stuck }, d2.defer)
    for (let i = 0; i < 3; i++) d2.flush()
    expect(stuck.scrollTo).not.toHaveBeenCalled() // still waiting after 3 turns
    d2.flush()
    expect(stuck.scrollTo).toHaveBeenCalledTimes(1) // the 4th turn writes anyway
    expect(d2.queue).toHaveLength(0)
  })

  it('a newer write supersedes a deferred one', () => {
    const el = makeEl(); const { defer, flush } = makeDefer()
    const instance = { scrollElement: el }
    scrollWithSizerGuard(3800, {}, instance, defer)
    scrollWithSizerGuard(100, {}, instance, defer)
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    flush()
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    expect(el.scrollTo).toHaveBeenLastCalledWith({ top: 100, behavior: undefined })
  })

  it('defers when only the adjustment pushes the target past the range (the prepend case)', () => {
    const el = makeEl(); const { defer, flush } = makeDefer()
    scrollWithSizerGuard(500, { adjustments: 3000 }, { scrollElement: el }, defer)
    expect(el.scrollTo).not.toHaveBeenCalled()
    el.scrollHeight = 4200
    flush()
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 3500, behavior: undefined })
  })

  it('tolerates one pixel past the maximum but defers two', () => {
    const el = makeEl(); const { defer, queue } = makeDefer()
    scrollWithSizerGuard(601, {}, { scrollElement: el }, defer)
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    scrollWithSizerGuard(602, {}, { scrollElement: el }, defer)
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    expect(queue).toHaveLength(1)
  })

  it('a newer deferred write supersedes an older deferred one, per element', () => {
    const a = makeEl(); const b = makeEl(); const { defer, flush } = makeDefer()
    scrollWithSizerGuard(3800, {}, { scrollElement: a }, defer)
    scrollWithSizerGuard(3900, {}, { scrollElement: b }, defer)
    scrollWithSizerGuard(4000, {}, { scrollElement: a }, defer)
    a.scrollHeight = 5000; b.scrollHeight = 5000
    flush()
    expect(a.scrollTo).toHaveBeenCalledTimes(1)
    expect(a.scrollTo).toHaveBeenCalledWith({ top: 4000, behavior: undefined })
    expect(b.scrollTo).toHaveBeenCalledWith({ top: 3900, behavior: undefined })
  })

  it('a smooth write supersedes a deferred one', () => {
    const el = makeEl(); const { defer, flush } = makeDefer()
    scrollWithSizerGuard(3800, {}, { scrollElement: el }, defer)
    scrollWithSizerGuard(100, { behavior: 'smooth' }, { scrollElement: el }, defer)
    flush()
    expect(el.scrollTo).toHaveBeenCalledTimes(1)
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 100, behavior: 'smooth' })
  })

  it('leaves smooth scrolls alone', () => {
    const el = makeEl(); const { defer, queue } = makeDefer()
    scrollWithSizerGuard(3800, { behavior: 'smooth' }, { scrollElement: el }, defer)
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 3800, behavior: 'smooth' })
    expect(queue).toHaveLength(0)
  })

  it('writes immediately when the element has no layout yet (hidden / jsdom)', () => {
    const el = makeEl({ scrollHeight: 0, clientHeight: 0 }); const { defer, queue } = makeDefer()
    scrollWithSizerGuard(300, {}, { scrollElement: el }, defer)
    expect(el.scrollTo).toHaveBeenCalledWith({ top: 300, behavior: undefined })
    expect(queue).toHaveLength(0)
  })

  it('ignores a missing scroll element', () => {
    const { defer } = makeDefer()
    expect(() => scrollWithSizerGuard(10, {}, { scrollElement: null }, defer)).not.toThrow()
  })
})

describe('shouldAdjustForItemSizeChange (issue #2030 stage 3 PR 3D)', () => {
  const instance = (offset, known = []) => ({ getScrollOffset: () => offset, scrollAdjustments: 0, itemSizeCache: new Map(known.map(k => [k, 100])) })

  it('compensates a first measurement of a row that starts above the fold, not one below it', () => {
    expect(shouldAdjustForItemSizeChange({ key: 'a', start: 100, end: 260 }, 5, instance(500))).toBe(true)
    expect(shouldAdjustForItemSizeChange({ key: 'a', start: 600, end: 760 }, 5, instance(500))).toBe(false)
  })

  it('compensates a re-measured row wholly above the fold whatever the scroll direction (the library skips it while "backward")', () => {
    expect(shouldAdjustForItemSizeChange({ key: 'a', start: 100, end: 260 }, 5, instance(500, ['a']))).toBe(true)
  })

  it('does not compensate a re-measured row that spans the fold (it grows below the anchor point)', () => {
    expect(shouldAdjustForItemSizeChange({ key: 'a', start: 400, end: 560 }, 5, instance(500, ['a']))).toBe(false)
  })

  it('counts pending adjustments in the offset', () => {
    const inst = { getScrollOffset: () => 500, scrollAdjustments: 100, itemSizeCache: new Map([['a', 100]]) }
    expect(shouldAdjustForItemSizeChange({ key: 'a', start: 400, end: 560 }, 5, inst)).toBe(true)
  })
})
