import { vi } from 'vitest'

// Issue #1748 (stage: offset-model): jsdom has no real layout engine, so every element's
// getBoundingClientRect()/offsetWidth/offsetHeight is 0 — @tanstack/vue-virtual's own
// calculateRange() short-circuits to an empty range whenever its observed scroll-container size
// is 0, regardless of `overscan`. Tests that render a virtualized component need a ResizeObserver
// reporting a plausible non-zero viewport, or its rows never appear. Scoped per-test-file (via
// vi.stubGlobal, undone by vi.unstubAllGlobals in the returned teardown) rather than added to
// vitest.setup.js globally — InputArea.test.js's offscreen-resize-clone tests specifically assert
// behavior when ResizeObserver is undefined ("default jsdom env"), so a global polyfill would
// break them.
export function stubResizeObserver({ width = 800, height = 600 } = {}) {
  const observers = new Set()

  class MockResizeObserver {
    constructor(callback) {
      this.callback = callback
      this.targets = new Set()
      observers.add(this)
    }
    observe(target) {
      this.targets.add(target)
      const rect = typeof target.getBoundingClientRect === 'function' ? target.getBoundingClientRect() : {}
      this.callback([{
        target,
        borderBoxSize: [{ inlineSize: rect.width || width, blockSize: rect.height || height }],
      }])
    }
    unobserve(target) {
      this.targets.delete(target)
    }
    disconnect() {
      this.targets.clear()
      observers.delete(this)
    }
  }
  vi.stubGlobal('ResizeObserver', MockResizeObserver)

  // Lets a test simulate a mounted row's measured size changing after the fact (e.g. streaming
  // growth on the tail row) — the real ResizeObserver never fires spontaneously under jsdom.
  function triggerResize(target, { width: w = width, height: h = height } = {}) {
    for (const observer of observers) {
      if (observer.targets.has(target)) {
        observer.callback([{ target, borderBoxSize: [{ inlineSize: w, blockSize: h }] }])
      }
    }
  }

  return { restore: () => vi.unstubAllGlobals(), triggerResize }
}
