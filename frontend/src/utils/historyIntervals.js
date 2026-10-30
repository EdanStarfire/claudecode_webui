// Pure helpers of the interval model (issue #2030, stage 3 PR 3C-1).
//
// A session's loaded history is at most TWO disjoint intervals of the history file: the live
// SUFFIX (the newest window plus whatever backfill prepended; contiguous to the end, held in the
// ordinary per-session message/tool state) and at most ONE pinned ISLAND (a window fetched by
// `around=` for a jump, grown by `before=` / `after=` pages; held in its own record target in
// message.js). A new jump replaces the island; when the two touch they merge into the suffix.
// Cursors are opaque; the file-backed reader encodes `b:<offset>`, used here for sizing and the
// touch test only (without it the touch test falls back to record overlap / `has_more_after`).
import { cursorOffset } from '@/utils/turnNavigation'

/** Interim cap (history-file bytes) on what one island may hold; stage 4 replaces it with eviction. */
export const ISLAND_MAX_BYTES = 64 * 1024 * 1024
/** Budget of one `after=` page ("Load newer") and of one island `before=` page. */
export const ISLAND_PAGE_BYTES = 256 * 1024

/** History-file bytes covered by `[startCursor, endCursor)`, or null for non-offset cursors. */
export function windowBytes(startCursor, endCursor) {
  const start = cursorOffset(startCursor)
  const end = cursorOffset(endCursor)
  return start == null || end == null ? null : Math.max(0, end - start)
}

/** True when the island holds more than its interim cap (paging stops; edges stay visible). */
export function islandIsFull(island) {
  return !!island && (island.bytes || 0) >= ISLAND_MAX_BYTES
}

/**
 * Do the island and the suffix touch (or overlap)? `hasMoreAfter === false` means the island's
 * forward read ran to the end of the file, which necessarily reaches the suffix. `overlapsLoaded`
 * is the store's id-overlap result for cursors that carry no offset.
 */
export function intervalsTouch(island, suffix, overlapsLoaded = false) {
  if (!island || !suffix) return false
  if (!island.hasMoreAfter) return true
  if (overlapsLoaded) return true
  const islandEnd = cursorOffset(island.endCursor)
  const suffixStart = cursorOffset(suffix.startCursor)
  return islandEnd != null && suffixStart != null && islandEnd >= suffixStart
}

/** `{ fromCursor, toCursor, approxBytes }` of the not-loaded stretch between island and suffix. */
export function gapBetween(island, suffix) {
  if (!island || !suffix) return null
  const bytes = windowBytes(island.endCursor, suffix.startCursor)
  return { fromCursor: island.endCursor, toCursor: suffix.startCursor, approxBytes: bytes }
}

/** Human size used by the gap row and the slow-link confirm ("about 0.5 MB"). */
export function formatHistorySize(bytes) {
  if (bytes == null) return null
  if (bytes < 100 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

/** Default size of an `around=` window (server default 384 KiB before + 128 KiB after). */
export const AROUND_WINDOW_FILE_BYTES = 512 * 1024
