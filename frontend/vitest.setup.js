import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'

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
