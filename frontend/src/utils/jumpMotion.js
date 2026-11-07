// Jump motion for the message list (issue #2342): planning, easing and a cancellable rAF tween.
// No Vue; clock, rAF and matchMedia are injectable so the unit tests run on fake timers.
//
// A deliberate jump (prev/next turn, search hit, gutter chip, deep link, "view in context") used to
// teleport. It now moves like this:
//   - short (<= SHORT_MAX_VIEWPORTS away): an eased JS tween from the current offset to the target;
//   - long: an instant move to a point APPROACH_VIEWPORTS before the target on the side the user came
//     from, then a short eased settle, so only rows near the target are ever mounted (never a native
//     'smooth' scroll: it is compositor-driven and sweeps across thousands of unmounted rows);
//   - the caller always finishes with the exact scrollToIndex + mount-confirm (landing exactness).
// Every frame re-reads the target offset (rows near the target re-measure while they mount).

export const SHORT_MAX_VIEWPORTS = 3
export const SHORT_MAX_MS = 350
export const APPROACH_VIEWPORTS = 1.5
export const SETTLE_MS = 300
export const CUE_MS = 1100

const MIN_MOVE_PX = 2 // closer than this is not worth animating
const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '])

export const easeOutCubic = (p) => 1 - Math.pow(1 - p, 3)
export const easeInOutCubic = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2)

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

/** Duration of a short jump `viewports` screens long. */
export function shortDuration(viewports) {
  return Math.min(SHORT_MAX_MS, 140 + 70 * viewports)
}

/**
 * `{ kind: 'instant' }` | `{ kind: 'short', viewports, up, duration }` | `{ kind: 'long', viewports, up, duration }`.
 * `up` = the target is above the current offset.
 */
export function planJump({ current, target, viewport }) {
  const distance = Math.abs(target - current)
  if (!(viewport > 0) || !(distance >= MIN_MOVE_PX)) return { kind: 'instant' }
  const viewports = distance / viewport
  const up = target < current
  if (viewports <= SHORT_MAX_VIEWPORTS) return { kind: 'short', viewports, up, duration: shortDuration(viewports) }
  return { kind: 'long', viewports, up, duration: SETTLE_MS }
}

/** Where a long jump lands first: APPROACH_VIEWPORTS before the target, on the side the user came from. */
export function approachOffset({ target, up, viewport, max }) {
  return clamp(target - (up ? -1 : 1) * APPROACH_VIEWPORTS * viewport, 0, Math.max(0, max))
}

export function prefersReducedMotion(matchMedia = typeof window !== 'undefined' ? window.matchMedia?.bind(window) : null) {
  try {
    return !!matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
  } catch {
    return false
  }
}

function isTextEntryTarget(el) {
  if (!el) return false
  if (el.isContentEditable) return true
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT'
}

/**
 * One jump animation at a time. `begin(el)` supersedes the previous handle and attaches the user-input
 * listeners (wheel / touchstart / mousedown on the scroller, scroll keys on the window) for the handle's
 * lifetime; any of them cancels with reason 'user' (the user wins: nothing corrects the position afterwards).
 *
 * handle: `{ cancelled, reason, tween(opts) -> Promise<'done'|'cancelled'>, end() }`
 */
export function createJumpMotion({
  now = () => performance.now(),
  raf = (cb) => requestAnimationFrame(cb),
  caf = (id) => cancelAnimationFrame(id),
} = {}) {
  let current = null

  function cancel(reason = 'cancel') {
    current?.cancel(reason)
  }

  // `onCancel(reason)` runs synchronously when the handle is cancelled by anything but a newer jump.
  function begin(el, { onCancel } = {}) {
    cancel('superseded')
    const keyTarget = el.ownerDocument?.defaultView || (typeof window !== 'undefined' ? window : null)
    const doc = el.ownerDocument
    const handle = { cancelled: false, reason: null, ended: false }
    let rafId = null
    let resolveTween = null

    const onUser = () => handle.cancel('user')
    const onKey = (e) => {
      if (!SCROLL_KEYS.has(e.key)) return
      if (isTextEntryTarget(doc?.activeElement)) return
      handle.cancel('user')
    }
    const detach = () => {
      for (const type of ['wheel', 'touchstart', 'mousedown']) el.removeEventListener(type, onUser)
      keyTarget?.removeEventListener('keydown', onKey)
    }
    for (const type of ['wheel', 'touchstart', 'mousedown']) el.addEventListener(type, onUser, { passive: true })
    keyTarget?.addEventListener('keydown', onKey, { passive: true })

    handle.cancel = (reason) => {
      if (handle.cancelled || handle.ended) return
      handle.cancelled = true
      handle.reason = reason
      if (rafId != null) { caf(rafId); rafId = null }
      detach()
      if (current === handle) current = null
      if (reason !== 'superseded') onCancel?.(reason)
      const resolve = resolveTween
      resolveTween = null
      resolve?.('cancelled')
    }

    handle.end = () => {
      if (handle.ended) return
      handle.ended = true
      if (rafId != null) { caf(rafId); rafId = null }
      detach()
      if (current === handle) current = null
    }

    // Eased write from `from` to `resolveTarget()` (re-read every frame); the last frame writes the target itself.
    handle.tween = ({ from, resolveTarget, duration, ease, onFrame }) => new Promise((resolve) => {
      if (handle.cancelled) { resolve('cancelled'); return }
      const t0 = now()
      resolveTween = resolve
      const step = () => {
        rafId = null
        if (handle.cancelled) return
        const p = duration > 0 ? Math.min(1, (now() - t0) / duration) : 1
        const to = resolveTarget()
        const top = p >= 1 ? to : from + (to - from) * ease(p)
        if (typeof el.scrollTo === 'function') el.scrollTo({ top, behavior: 'auto' })
        else el.scrollTop = top
        onFrame?.(p)
        if (p < 1) { rafId = raf(step); return }
        resolveTween = null
        resolve('done')
      }
      rafId = raf(step)
    })

    current = handle
    return handle
  }

  return { begin, cancel, isActive: () => current != null }
}
