// Pure helpers for turn navigation over the server history index (issue #2030, stage 2 PR B).
//
// The index lists EVERY display-user turn of the history as `[cursor, message_id, size_hint]`
// (file order; `size_hint` is the record's byte position — a hint, like the `b:<offset>` progress
// use of cursors). The browser holds a contiguous suffix of the history starting at
// `startCursor`; a turn whose position lies before it is not loaded.

/** Estimated wire bytes above which a jump asks first (fast links); slow links always ask. */
export const JUMP_CONFIRM_WIRE_BYTES = 2 * 1024 * 1024
/** Decoded/wire ratio assumed until a history page has been measured. */
export const DEFAULT_WIRE_RATIO = 5

/** Byte offset of a `b:<offset>` cursor, or null when the cursor isn't of that form. */
export function cursorOffset(cursor) {
  const match = /^b:(\d+)$/.exec(cursor || '')
  return match ? Number(match[1]) : null
}

/**
 * The newest index turn that starts before `startOffset` (i.e. is not in the loaded suffix), as
 * `{ position, cursor, messageId, sizeHint }` (`position` is its 0-based index in `turns`), or
 * null when no older turn exists. `turns` is in file order, so this is a binary search.
 */
export function findTurnBefore(turns, startOffset) {
  let low = 0
  let high = turns.length - 1
  let found = -1
  while (low <= high) {
    const mid = (low + high) >> 1
    if (turns[mid][2] < startOffset) { found = mid; low = mid + 1 } else { high = mid - 1 }
  }
  if (found < 0) return null
  const [cursor, messageId, sizeHint] = turns[found]
  return { position: found, cursor, messageId, sizeHint }
}

/** Expected download (wire bytes) of the history between the target turn and the loaded start. */
export function estimateJumpWireBytes(startOffset, targetOffset, wireRatio) {
  const distance = Math.max(0, startOffset - targetOffset)
  return distance / (wireRatio > 0 ? wireRatio : DEFAULT_WIRE_RATIO)
}

/** Slow links always confirm; otherwise only a jump expected to download more than the cap. */
export function jumpNeedsConfirm({ slowLink, estimatedWireBytes }) {
  return !!slowLink || estimatedWireBytes > JUMP_CONFIRM_WIRE_BYTES
}

export function formatJumpSize(bytes) {
  if (bytes < 100 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Fraction (0..1) of the jump's byte distance covered by the loaded suffix so far. */
export function jumpProgress(startCursor, jump) {
  const start = cursorOffset(startCursor)
  if (start == null || !jump || !(jump.fromOffset > jump.toOffset)) return null
  return Math.min(1, Math.max(0, (jump.fromOffset - start) / (jump.fromOffset - jump.toOffset)))
}
