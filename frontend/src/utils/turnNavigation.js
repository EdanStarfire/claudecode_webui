// Pure helpers for turn navigation over the server history index (issue #2030, stage 2 PR B).
//
// The index lists EVERY display-user turn of the history as `[cursor, message_id, size_hint]`
// (file order; `size_hint` is the record's byte position — a hint, like the `b:<offset>` progress
// use of cursors). The browser holds a contiguous suffix of the history starting at
// `startCursor`; a turn whose position lies before it is not loaded.

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

/**
 * The first index turn that starts at or after `offset` (i.e. is not before it), as
 * `{ position, cursor, messageId, sizeHint }`, or null when none exists.
 */
export function findTurnAtOrAfter(turns, offset) {
  let low = 0
  let high = turns.length - 1
  let found = -1
  while (low <= high) {
    const mid = (low + high) >> 1
    if (turns[mid][2] >= offset) { found = mid; high = mid - 1 } else { low = mid + 1 }
  }
  if (found < 0) return null
  const [cursor, messageId, sizeHint] = turns[found]
  return { position: found, cursor, messageId, sizeHint }
}

/** Only a slow link asks before a jump: one bounded window (about 0.5 MB) is the whole cost. */
export function jumpNeedsConfirm({ slowLink }) {
  return !!slowLink
}

export function formatJumpSize(bytes) {
  if (bytes < 100 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * One Prev/Next turn step over the loaded intervals plus the server history index (issue #2030
 * stage 3 PR 3C-1). The loaded rows are one or two intervals (an island above the suffix, a `gap`
 * row between); a turn "above"/"below" the viewport only counts when it lies in the SAME interval
 * as the viewport edge, otherwise the neighbouring turn comes from the index (it may sit in the gap,
 * unloaded, or be the adjacent interval's edge turn, loaded).
 *
 * `state`: `{ boundaries, gapIndex, top, bottom, viewTop, viewBottom, turns, indexStatus, complete, suffixStartOffset,
 * islandStartOffset, islandEndOffset, rowOfTurn }` — boundaries = ascending row indices of turn
 * rows, gapIndex = row of the gap (-1 none), top/bottom = mounted rows, viewTop/viewBottom = rows on screen
 * (used across a gap), turns = index turns (or
 * null while not ready), rowOfTurn(messageId) = row index of a loaded turn or null.
 * Returns `{ kind: 'loaded', index }` | `{ kind: 'unloaded', cursor, messageId, ordinal, sizeHint }` |
 * `{ kind: 'pending', reason: 'idle' | 'loading' | 'unavailable' }` | null (no such turn).
 */
export function planTurnStep(direction, state) {
  const { boundaries, gapIndex, turns, indexStatus, complete, rowOfTurn } = state
  const hasGap = gapIndex >= 0
  // Without a gap the mounted row range decides (the stage 2 behaviour, unchanged); across a gap the
  // overscan rows would put the viewport in the wrong interval, so the rows really on screen decide.
  const top = hasGap ? (state.viewTop ?? state.top) : state.top
  const bottom = hasGap ? (state.viewBottom ?? state.bottom) : state.bottom
  const needIndex = () => {
    if (!turns) return { kind: 'pending', reason: indexStatus && indexStatus !== 'ready' ? indexStatus : 'idle' }
    return null
  }
  const resolve = (turn) => {
    if (!turn) return null
    const row = rowOfTurn(turn.messageId)
    if (row != null) return { kind: 'loaded', index: row }
    return { kind: 'unloaded', cursor: turn.cursor, messageId: turn.messageId, ordinal: turn.position + 1, sizeHint: turn.sizeHint }
  }

  if (direction === 'prev') {
    // (the gap row itself at the top of the screen means the content below it, the suffix, is what is read)
    const inSuffix = hasGap && top >= gapIndex
    for (let i = boundaries.length - 1; i >= 0; i--) {
      const b = boundaries[i]
      if (b < top && (!hasGap || (b > gapIndex) === inSuffix)) return { kind: 'loaded', index: b }
    }
    if (complete) return null
    const pending = needIndex()
    if (pending) return pending
    const refOffset = inSuffix || !hasGap ? state.suffixStartOffset : state.islandStartOffset
    if (refOffset == null) return { kind: 'pending', reason: 'unavailable' }
    return resolve(findTurnBefore(turns, refOffset))
  }

  const inSuffix = hasGap && bottom > gapIndex
  for (const b of boundaries) {
    if (b > bottom && (!hasGap || (b > gapIndex) === inSuffix)) return { kind: 'loaded', index: b }
  }
  // Nothing further in this interval: only an island has newer history that is not loaded.
  if (!hasGap || inSuffix || state.islandEndOffset == null) return null
  const pending = needIndex()
  if (pending) return pending
  return resolve(findTurnAtOrAfter(turns, state.islandEndOffset))
}
