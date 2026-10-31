// Scroll-driven history loading (issue #2030 stage 3 PR 3D): how far from an interval edge the next
// page is requested. Three viewport heights by default, widened to what the user would scroll through
// while one page is in flight (measured page latency x scroll speed toward the edge, with a margin)
// so a fast scroll does not reach the edge before the page does; capped so a flick cannot ask for
// pages far ahead of where it will land.

export const PREFETCH_VIEWPORTS = 3
export const MAX_PREFETCH_VIEWPORTS = 8
export const PREFETCH_LATENCY_MARGIN = 1.5
export const MAX_SPEED_PX_PER_MS = 20

/** Distance (px) from an edge within which its next page is requested. Speed in px/ms toward the edge. */
export function prefetchDistancePx(clientHeight, towardEdgeSpeedPxPerMs, latencyMs) {
  const base = PREFETCH_VIEWPORTS * clientHeight
  const lead = Math.max(0, towardEdgeSpeedPxPerMs || 0) * Math.max(0, latencyMs || 0) * PREFETCH_LATENCY_MARGIN + clientHeight
  return Math.min(MAX_PREFETCH_VIEWPORTS * clientHeight, Math.max(base, lead))
}

/** Smoothed signed scroll speed (px/ms; negative = up) from the previous sample; resets after a pause. */
export function nextScrollSpeed(prevSpeed, prevTop, prevAt, top, at) {
  const dt = at - prevAt
  if (!prevAt || !(dt > 0) || dt >= 500) return 0
  // A real flick stays well below MAX_SPEED_PX_PER_MS; a single-frame rewrite of scrollTop (an anchored
  // prepend, a jump) is not the user's speed.
  const instant = Math.max(-MAX_SPEED_PX_PER_MS, Math.min(MAX_SPEED_PX_PER_MS, (top - prevTop) / dt))
  return 0.5 * prevSpeed + 0.5 * instant
}
