import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'

// Issue #2030 stage 4 PR 4b — freeze tripwire. The message store inserts loaded history records
// through `markRaw(...)` and never mutates them afterwards (a changed record is replaced). Every
// suite therefore runs with `markRaw` deep-freezing plain objects: any hidden in-place write to a
// stored message throws (modules are strict) instead of silently not re-rendering. Arrays are
// left alone (the turn list is marked raw but is built and then only replaced).
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  const deepFreeze = (value) => {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {
      Object.freeze(value)
      for (const key of Object.keys(value)) deepFreeze(value[key])
    }
    return value
  }
  return {
    ...actual,
    markRaw: (value) => {
      const raw = actual.markRaw(value)
      return raw && typeof raw === 'object' && !Array.isArray(raw) ? deepFreeze(raw) : raw
    },
  }
})

// jsdom doesn't implement scrollIntoView; several components call it (e.g. permission
// auto-scroll in ActivityTimeline.vue) and it's a no-op layout concern in tests.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'log').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})
