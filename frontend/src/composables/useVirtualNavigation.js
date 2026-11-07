import { nextTick } from 'vue'

import {
  CUE_MS, approachOffset, clamp, createJumpMotion, easeInOutCubic, easeOutCubic, planJump, prefersReducedMotion,
} from '@/utils/jumpMotion'

// Issue #1748 (stage: offset-model): shared "jump to a displayableItems index" helper — the
// single mechanism behind SubagentGlobalGutter's chip clicks, PermissionQueue's "view in
// context", turn navigation, search hits and deep links. Per the plan (§5.5): never run a NATIVE
// smooth scroll through thousands of unrendered rows (compositor-driven: it sweeps blank spacer and
// mounts everything it passes), then confirm the target index is actually mounted before running any
// DOM-dependent follow-up (flash-highlight, scrollIntoView fine-centering, focus).
// Issue #2342: a deliberate jump now MOVES (utils/jumpMotion.js): a JS-driven eased tween for a short
// distance, and for a long one an instant move next to the target plus a short eased settle — only rows
// near the target ever mount. Every motion ends in the exact scrollToIndex + mount-confirm below, so the
// landing is as exact as the instant jump was. Corrections (`motion: 'none'`) stay instant.
//
// `virtualizerRef` is the Ref<Virtualizer> returned by @tanstack/vue-virtual's useVirtualizer().
const MAX_MOUNT_POLL_ATTEMPTS = 20
// A saturated main thread (a 90k-record session backfilling 4 MiB pages while the user jumps) can need
// longer than one poll budget for measured row sizes to settle: the jump is re-issued against the fresh
// measurements and polled again before it is given up (issue #2030 stage 3 PR 3C-1).
const MOUNT_ROUNDS = 3

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

/**
 * `hooks` (all optional; MessageList wires them):
 *   onMotionStart / onMotionEnd  the jump-open hold around an animated jump (sticky-to-bottom is re-derived at the end)
 *   onFrame                      called per animation frame (programmatic-scroll renewal)
 *   onJumpStart(index)           a new deliberate jump begins (cancels a pending pulse)
 *   onLanded(index)              the jump landed and was confirmed mounted (pulse the row)
 *   onUserCancel()               the user took over mid-animation
 *   onCue(cue | null)            long-jump cue `{ fromFrac, toFrac, up, label }`
 *   describeJump(index, base)    cue content `{ fromFrac, toFrac, up, turns, pct }` (`base` = the list-offset cue; fromIndex = the row it started on), or null for no cue
 */
export function useVirtualNavigation(virtualizerRef, hooks = {}) {
  const motionRunner = createJumpMotion()
  let cueTimer = null
  let epoch = 0 // bumped by cancelMotion: an instant jump that was awaiting its mount must not run its follow-ups afterwards

  let cueActive = false
  function setCue(cue) {
    clearTimeout(cueTimer)
    cueTimer = null
    if (!cue && !cueActive) return
    cueActive = !!cue
    hooks.onCue?.(cue)
    if (cue) cueTimer = setTimeout(() => { cueTimer = null; cueActive = false; hooks.onCue?.(null) }, CUE_MS)
  }

  /** Stops a running jump animation (session switch, unmount, deactivate): no landing correction, no cue. */
  function cancelMotion(reason = 'cancel') {
    epoch++
    motionRunner.cancel(reason)
    setCue(null)
    stopLibraryReconcile()
  }

  // scrollToIndex keeps re-aiming at its target for a few seconds (the virtualizer's own reconcile loop). A
  // cancelled jump must not be pulled back to its target by it: the user (or the view that went away) wins.
  function stopLibraryReconcile() {
    const virtualizer = virtualizerRef.value
    if (virtualizer && 'scrollState' in virtualizer) virtualizer.scrollState = null
  }

  // The offset a jump to `index` ends at, clamped to the reachable range. `align` is resolved once at the
  // start (an 'auto' resolved per frame would stop moving as soon as the row scrolls into view).
  function resolveOffset(virtualizer, index, align) {
    const resolved = virtualizer.getOffsetForIndex?.(index, align)
    if (!resolved) return null
    const el = virtualizer.scrollElement
    const max = Math.max(0, virtualizer.getTotalSize() - (el?.clientHeight || 0))
    return { offset: clamp(resolved[0], 0, max), align: resolved[1], max }
  }

  async function exactLanding(index, align, behavior, handle) {
    for (let round = 0; round < MOUNT_ROUNDS; round++) {
      const current = virtualizerRef.value
      if (!current || handle?.cancelled) return false
      forceFreshMeasurements(current)
      current.scrollToIndex(index, { align, behavior })
      const mounted = await waitForIndexMounted(index, MAX_MOUNT_POLL_ATTEMPTS, round === MOUNT_ROUNDS - 1 ? MOUNT_ROUNDS : 0)
      if (handle?.cancelled) return false
      if (mounted) return true
    }
    return false
  }

  /**
   * Scrolls so `index` is the current best-known target, then waits for it to actually mount.
   * Returns true once the index is confirmed mounted, false if the virtualizer/index is
   * unavailable or mounting couldn't be confirmed within the bounded retry budget, and null when the
   * jump was cancelled (the user scrolled, a newer jump started, the view went away): the row may
   * well exist, so a caller must not report it missing.
   *
   * `motion`: 'animate' (default; a deliberate jump) | 'none' (an instant correction: search keeper,
   * retries). `pulse` (default: true for 'animate') runs hooks.onLanded after a confirmed landing.
   */
  async function scrollToItemIndex(index, { align = 'auto', behavior = 'auto', motion = 'animate', pulse = motion !== 'none' } = {}) {
    const virtualizer = virtualizerRef.value
    if (!virtualizer || index == null || index < 0) return false
    if (motion === 'none') {
      motionRunner.cancel('superseded')
      return exactLanding(index, align, behavior)
    }

    hooks.onJumpStart?.(index)
    setCue(null) // a cue of the jump this one replaces no longer describes where the list is going
    const myEpoch = epoch
    const el = virtualizer.scrollElement
    forceFreshMeasurements(virtualizer)
    const target = el ? resolveOffset(virtualizer, index, align) : null
    const reduced = prefersReducedMotion()
    const viewport = el?.clientHeight || 0
    const from = el ? el.scrollTop : 0
    const plan = !reduced && target ? planJump({ current: from, target: target.offset, viewport }) : { kind: 'instant' }

    if (plan.kind === 'instant') {
      motionRunner.cancel('superseded')
      const landed = await exactLanding(index, align, behavior)
      if (myEpoch !== epoch) return null
      if (landed && pulse) hooks.onLanded?.(index)
      return landed
    }

    const handle = motionRunner.begin(el, { onCancel: stopLibraryReconcile })
    hooks.onMotionStart?.()
    try {
      const resolveTarget = () => resolveOffset(virtualizerRef.value || virtualizer, index, target.align)?.offset ?? target.offset
      let tweenFrom = from
      if (plan.kind === 'long') {
        showLongCue(virtualizer, el, index, from, target.offset, plan.up)
        const start = approachOffset({ target: target.offset, up: plan.up, viewport, max: target.max })
        el.scrollTo({ top: start, behavior: 'auto' })
        hooks.onFrame?.()
        const startRow = virtualizer.getVirtualItemForOffset?.(start)
        if (startRow) await waitForIndexMounted(startRow.index, MAX_MOUNT_POLL_ATTEMPTS, 0)
        tweenFrom = el.scrollTop
      }
      if (handle.cancelled) return finishCancelled(handle)
      const ease = plan.kind === 'long' ? easeOutCubic : easeInOutCubic
      const result = await handle.tween({ from: tweenFrom, resolveTarget, duration: plan.duration, ease, onFrame: hooks.onFrame })
      if (result !== 'done' || handle.cancelled) return finishCancelled(handle)
      const landed = await exactLanding(index, target.align, behavior, handle)
      if (handle.cancelled) return finishCancelled(handle)
      if (landed && pulse) hooks.onLanded?.(index)
      return landed
    } finally {
      handle.end()
      hooks.onMotionEnd?.()
    }
  }

  function finishCancelled(handle) {
    if (handle.reason === 'user') {
      setCue(null)
      hooks.onUserCancel?.()
    }
    return null
  }

  function showLongCue(virtualizer, el, index, fromOffset, toOffset, up) {
    if (!hooks.onCue) return
    const span = Math.max(1, virtualizer.getTotalSize() - el.clientHeight)
    const base = {
      fromFrac: clamp(fromOffset / span, 0, 1),
      toFrac: clamp(toOffset / span, 0, 1),
      up,
      pct: Math.max(1, Math.round((Math.abs(toOffset - fromOffset) / span) * 100)),
      turns: null,
      fromIndex: virtualizer.getVirtualItemForOffset?.(fromOffset)?.index ?? null,
    }
    const cue = hooks.describeJump ? hooks.describeJump(index, base) : base
    if (!cue) return
    const arrow = cue.up ? '↑' : '↓'
    const label = cue.turns != null && cue.turns > 0
      ? `${arrow} ${cue.turns} turn${cue.turns === 1 ? '' : 's'} · ${cue.pct}%`
      : `${arrow} ${cue.pct}%`
    setCue({ fromFrac: cue.fromFrac, toFrac: cue.toFrac, up: cue.up, label })
  }

  /**
   * Bounded nextTick/rAF retry against the virtualizer's own rendered range — not a fixed
   * timeout — per §5.4's guidance for the one-shot jump-navigation tolerance profile.
   */
  async function waitForIndexMounted(index, maxAttempts = MAX_MOUNT_POLL_ATTEMPTS, warnRounds = 1) {
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
    if (warnRounds > 0) console.warn(`[useVirtualNavigation] index ${index} did not mount within ${maxAttempts * warnRounds} attempts`)
    return false
  }

  return { scrollToItemIndex, waitForIndexMounted, cancelMotion, isAnimating: () => motionRunner.isActive() }
}
