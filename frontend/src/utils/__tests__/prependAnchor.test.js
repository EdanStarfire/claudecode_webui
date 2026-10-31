import { describe, it, expect } from 'vitest'
import { anchorCorrection, captureMessageAnchor } from '@/utils/prependAnchor'

const idOf = (row) => (row.type === 'message' ? row.id : null)

describe('captureMessageAnchor (issue #2030 stage 3 PR 3D)', () => {
  const rows = [{ type: 'date_separator' }, { type: 'message', id: 'm1' }, { type: 'message', id: 'm2' }]
  const starts = [0, 40, 400]

  it('skips rows without a message (the volatile top separator) and records the offset inside the anchor row', () => {
    expect(captureMessageAnchor(rows, 10, (i) => starts[i], 0, idOf)).toEqual({ id: 'm1', within: -30 })
    expect(captureMessageAnchor(rows, 450, (i) => starts[i], 2, idOf)).toEqual({ id: 'm2', within: 50 })
  })

  it('looks at most 8 rows ahead of the viewport row', () => {
    const many = [...Array(10).fill({ type: 'date_separator' }), { type: 'message', id: 'late' }]
    expect(captureMessageAnchor(many, 0, () => 0, 0, idOf)).toBeNull()
    expect(captureMessageAnchor(many, 0, () => 0, 3, idOf)).toEqual({ id: 'late', within: 0 })
  })

  it('returns null when there is no message row nearby or no measurement', () => {
    expect(captureMessageAnchor([{ type: 'date_separator' }], 0, () => 0, 0, idOf)).toBeNull()
    expect(captureMessageAnchor(rows, 0, () => null, 0, idOf)).toBeNull()
  })
})

describe('anchorCorrection', () => {
  const anchor = { id: 'm1', within: -30 }

  it('is the distance the anchor message moved, so the view can go back to it', () => {
    // m1 now starts 3000 px lower: the viewport (still at 10) must move down by 3000
    expect(anchorCorrection(anchor, () => 7, () => 3040, 10)).toBe(3000)
  })

  it('honours a different threshold', () => {
    expect(anchorCorrection(anchor, () => 1, () => 150, 10, 50)).toBe(110)
  })

  it('does nothing for a small displacement (the user\'s own scrolling), an unknown message or a missing measurement', () => {
    expect(anchorCorrection(anchor, () => 1, () => 40, 10)).toBe(0)
    expect(anchorCorrection(anchor, () => 1, () => 150, 10)).toBe(0) // 110 px: a user wheel burst, not a page
    expect(anchorCorrection(anchor, () => null, () => 0, 10)).toBe(0)
    expect(anchorCorrection(anchor, () => 1, () => null, 10)).toBe(0)
    expect(anchorCorrection(null, () => 1, () => 0, 10)).toBe(0)
  })
})
