import { nextTick } from 'vue'

// Issue #1748 (stage: offset-model): shared "jump to a displayableItems index" helper — the
// single mechanism behind SubagentGlobalGutter's chip clicks, PermissionQueue's "view in
// context", and (once virtualized) scroll-position restore. Per the plan (§5.5): jump to the
// virtualizer's current best-known offset immediately (never smooth-scroll through thousands of
// unrendered rows), then confirm the target index is actually mounted before running any
// DOM-dependent follow-up (flash-highlight, scrollIntoView fine-centering, focus).
//
// `virtualizerRef` is the Ref<Virtualizer> returned by @tanstack/vue-virtual's useVirtualizer().
const MAX_MOUNT_POLL_ATTEMPTS = 20

/**
 * `measurementsCache`/`scrollToIndex`/`getOffsetForIndex` all resolve against
 * `measurementsCache` as a plain property, not the memoized `getMeasurements()` getter — so it
 * can be stale (e.g. right after a count change) unless something already forced a fresh
 * measurement pass this tick. `getTotalSize()` is that forcing call; it also returns the value
 * a caller usually wants anyway, so this is the single choke point every call site should use
 * instead of each re-deriving the same "why do I need this" comment.
 */
export function forceFreshMeasurements(virtualizer) {
  return virtualizer.getTotalSize()
}

export function useVirtualNavigation(virtualizerRef) {
  /**
   * Scrolls so `index` is the current best-known target, then waits for it to actually mount.
   * Returns true once the index is confirmed mounted, false if the virtualizer/index is
   * unavailable or mounting couldn't be confirmed within the bounded retry budget.
   */
  async function scrollToItemIndex(index, { align = 'auto', behavior = 'auto' } = {}) {
    const virtualizer = virtualizerRef.value
    if (!virtualizer || index == null || index < 0) return false
    forceFreshMeasurements(virtualizer)
    virtualizer.scrollToIndex(index, { align, behavior })
    return waitForIndexMounted(index)
  }

  /**
   * Bounded nextTick/rAF retry against the virtualizer's own rendered range — not a fixed
   * timeout — per §5.4's guidance for the one-shot jump-navigation tolerance profile.
   */
  async function waitForIndexMounted(index, maxAttempts = MAX_MOUNT_POLL_ATTEMPTS) {
    const virtualizer = virtualizerRef.value
    if (!virtualizer) return false
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      await nextTick()
      await new Promise(resolve => requestAnimationFrame(resolve))
      const current = virtualizerRef.value
      if (!current) return false
      if (current.getVirtualItems().some(item => item.index === index)) return true
    }
    // At a real overscan value a jump target can legitimately take a couple of render passes to
    // mount (scrollToIndex → measurement pass → possibly-corrected offset) — this warning is the
    // trail for when that bounded retry budget is actually exhausted, so a silent false return
    // doesn't just read as "the jump button silently doesn't work" with no diagnostic signal.
    console.warn(`[useVirtualNavigation] index ${index} did not mount within ${maxAttempts} attempts`)
    return false
  }

  return { scrollToItemIndex, waitForIndexMounted }
}
