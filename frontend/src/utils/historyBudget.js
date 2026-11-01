// Memory budget of the loaded chat history (issue #2030 stage 4 PR 4d).
//
// The unit is bytes of the history FILE covered by the loaded records (the unit the server pages,
// the banner and the cursors already speak: `totalBytes - startOffset`; for the island
// `windowBytes(start, end)`). After stage 4 PR 4b the heap is ~1.4 x that, i.e. ~90 MB on a desktop
// and ~45 MB on a phone at the budget. The budget bounds what the BACKGROUND extender loads and what
// an idle trim shrinks the oldest end back to; a page the user asks for (scrolling toward the edge,
// the banner's button, "Load full history") is never refused by it.

export const MIB = 1024 * 1024
export const DESKTOP_BUDGET_BYTES = 64 * MIB
export const PHONE_BUDGET_BYTES = 32 * MIB
/** An idle trim shrinks the loaded history to this fraction of the budget (one live record must not re-trigger it). */
export const TRIM_TARGET_FRACTION = 0.75
/** Trims of one session are at least this far apart. */
export const TRIM_MIN_INTERVAL_MS = 30000
/** How soon a pending trim is re-evaluated while the conditions (idle, no gesture, far from the edge) are not met. */
export const TRIM_RETRY_MS = 5000
/** A trim only removes rows that end at least this many viewport heights above the viewport (scroll prefetch fires within 8). */
export const TRIM_GUARD_VIEWPORTS = 12
/** localStorage key of the support/test override, in MiB. */
export const BUDGET_OVERRIDE_KEY = 'historyBudgetMiB'
const PHONE_MAX_WIDTH = 768 // mirrors stores/ui.js isMobile

function readOverride(storage) {
  try {
    const value = Number(storage?.getItem(BUDGET_OVERRIDE_KEY))
    return Number.isFinite(value) && value > 0 ? value * MIB : null
  } catch {
    return null // storage unavailable (private mode, tests)
  }
}

/** Phone-class device: narrow viewport, <= 4 GB device memory, or a coarse primary pointer. */
export function isPhoneProfile(env = {}) {
  const nav = env.navigator ?? (typeof navigator !== 'undefined' ? navigator : null)
  const width = env.innerWidth ?? (typeof window !== 'undefined' ? window.innerWidth : Infinity)
  const matchMedia = env.matchMedia ?? (typeof window !== 'undefined' ? window.matchMedia?.bind(window) : null)
  if (width < PHONE_MAX_WIDTH) return true
  if (typeof nav?.deviceMemory === 'number' && nav.deviceMemory <= 4) return true
  try {
    return !!matchMedia?.('(pointer: coarse)')?.matches
  } catch {
    return false
  }
}

/** The loaded-history budget in bytes of history file. `env` is injectable for tests. */
export function getHistoryBudgetBytes(env = {}) {
  const storage = env.storage ?? (typeof localStorage !== 'undefined' ? localStorage : null)
  return readOverride(storage) ?? (isPhoneProfile(env) ? PHONE_BUDGET_BYTES : DESKTOP_BUDGET_BYTES)
}

/** What a session that is no longer the current one keeps (the newest part). */
export function backgroundKeepBytes(budget) {
  return Math.min(8 * MIB, Math.floor(budget / 4))
}

/** Where an idle trim stops (hysteresis). */
export function trimTargetBytes(budget) {
  return Math.floor(budget * TRIM_TARGET_FRACTION)
}
