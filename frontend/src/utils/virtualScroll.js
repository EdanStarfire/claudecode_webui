// Scroll writer for the message list's virtualizer (see MessageList.vue `scrollToFn`).
//
// @tanstack/vue-virtual writes its anchored-prepend correction (and the size-change compensation
// that follows it) to the DOM BEFORE Vue has patched the spacer to the new total height. The
// browser clamps such a write to the old maximum scroll position, the virtualizer then believes
// it is at the end of the list and keeps compensating from that wrong base, and the rows end up
// one page away from where the user was looking. A write past the reachable range therefore waits
// (a few render turns at most) until the DOM has grown, and a newer write supersedes it.

const latestWrite = new WeakMap()
const MAX_DEFERRALS = 4

const beyondReach = (el, top) => el.clientHeight > 0 && top > el.scrollHeight - el.clientHeight + 1

export function scrollWithSizerGuard(offset, { adjustments, behavior } = {}, instance, defer) {
  const el = instance.scrollElement
  if (!el || typeof el.scrollTo !== 'function') return
  const top = offset + (adjustments || 0)
  const write = () => el.scrollTo({ top, behavior })
  const seq = (latestWrite.get(el) || 0) + 1
  latestWrite.set(el, seq)
  if (behavior === 'smooth' || !beyondReach(el, top)) { write(); return }
  const attempt = (n) => defer(() => {
    if (latestWrite.get(el) !== seq) return
    if (n < MAX_DEFERRALS && beyondReach(el, top)) attempt(n + 1)
    else write()
  })
  attempt(1)
}

// A re-measured row ENTIRELY above the viewport changes the position of everything below it. The
// library skips compensating those re-measurements while the scroll direction reads "backward" (to
// avoid a cascade of corrections while scrolling up through unmeasured rows), so a user scrolling up
// while rows above settle to their real size (newly prepended rows render markdown, images ...) sees
// the view jump by the sum. First measurements keep the library's rule (any row starting above the
// fold); re-measurements of rows wholly above the fold are compensated whatever the direction.
export function shouldAdjustForItemSizeChange(item, delta, instance) {
  const offset = instance.getScrollOffset() + instance.scrollAdjustments
  const firstMeasure = !instance.itemSizeCache.has(item.key)
  return firstMeasure ? item.start < offset : item.end <= offset
}
