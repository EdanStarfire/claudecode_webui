// Keeps the viewport on the same message when an older page is prepended (issue #2030 stage 3 PR 3D).
//
// The virtualizer anchors a prepend on the ROW at the viewport top, by row key. When the user is at
// (or within one row of) the very top, that row is exactly the one the merge rewrites: the topmost
// date separator of the old content keeps its key but becomes the first row of the new, older page
// (the old rows move below it), so the "anchor" resolves to index 0 again and the view stays at the
// top of the new content — one page away from where the user was. The anchor here is the first row
// that carries a MESSAGE (an id the merge never renames).

/** `{ id, within }`: the first message row at/after the viewport top and the viewport offset inside it. */
export function captureMessageAnchor(rows, offset, getStart, startIndex, rowMessageId) {
  for (let index = startIndex; index < rows.length && index < startIndex + 8; index++) {
    const id = rowMessageId(rows[index])
    if (id == null) continue
    const start = getStart(index)
    if (start == null) return null
    return { id, within: offset - start }
  }
  return null
}

/** Pixels the viewport must move to put the anchor message back where it was (0 when unknown / not needed). */
export function anchorCorrection(anchor, indexOfMessage, getStart, currentOffset, minPx = 200) {
  if (!anchor) return 0
  const index = indexOfMessage(anchor.id)
  if (index == null) return 0
  const start = getStart(index)
  if (start == null) return 0
  const delta = start + anchor.within - currentOffset
  return Math.abs(delta) >= minPx ? delta : 0
}
