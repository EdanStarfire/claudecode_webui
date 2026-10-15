import { describe, it, expect, beforeEach } from 'vitest'
import { applyHighlight, clearHighlight } from '@/search/highlightMatch'

function makeContainer(text) {
  const el = document.createElement('div')
  el.textContent = text
  document.body.appendChild(el)
  return el
}

describe('highlightMatch', () => {
  let container

  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('wraps the first case-insensitive occurrence in a <mark>', () => {
    container = makeContainer('find the Needle in the haystack')
    const applied = applyHighlight(container, 'needle')
    expect(applied).toBe(true)

    const mark = container.querySelector('mark.message-search-highlight')
    expect(mark).not.toBeNull()
    expect(mark.textContent).toBe('Needle')
    expect(container.textContent).toBe('find the Needle in the haystack')
  })

  it('clearHighlight unwraps the mark and merges text nodes back (idempotent)', () => {
    container = makeContainer('find the needle in the haystack')
    applyHighlight(container, 'needle')
    expect(container.querySelector('mark')).not.toBeNull()

    clearHighlight(container)
    expect(container.querySelector('mark')).toBeNull()
    expect(container.textContent).toBe('find the needle in the haystack')

    // Clearing again is a no-op, not an error.
    expect(() => clearHighlight(container)).not.toThrow()
  })

  it('highlights the Nth occurrence (0-based) when given an occurrenceIndex, within one text node', () => {
    container = makeContainer('cat cat cat')
    expect(applyHighlight(container, 'cat', 1)).toBe(true)
    const marks = container.querySelectorAll('mark.message-search-highlight')
    expect(marks).toHaveLength(1)
    expect(container.textContent).toBe('cat cat cat')
    // The second "cat" (index 1) is the one wrapped — confirm position via surrounding text.
    expect(container.innerHTML).toContain('cat <mark class="message-search-highlight">cat</mark> cat')
  })

  it('counts occurrences across multiple text nodes in DOM order', () => {
    container = document.createElement('div')
    container.innerHTML = '<span>needle one</span><span>needle two</span>'
    document.body.appendChild(container)

    expect(applyHighlight(container, 'needle', 1)).toBe(true)
    const mark = container.querySelector('mark.message-search-highlight')
    expect(mark).not.toBeNull()
    expect(mark.closest('span').textContent).toContain('needle two')
  })

  it('returns false when occurrenceIndex is out of range', () => {
    container = makeContainer('only one needle here')
    expect(applyHighlight(container, 'needle', 1)).toBe(false)
    expect(container.querySelector('mark')).toBeNull()
  })

  it('is a no-op when the substring is absent', () => {
    container = makeContainer('nothing to see here')
    const applied = applyHighlight(container, 'needle')
    expect(applied).toBe(false)
    expect(container.querySelector('mark')).toBeNull()
  })

  it('does not wrap text inside skipped tags (e.g. button/input/script)', () => {
    container = document.createElement('div')
    container.innerHTML = '<button>needle button</button><span>needle span</span>'
    document.body.appendChild(container)

    applyHighlight(container, 'needle')
    const mark = container.querySelector('mark.message-search-highlight')
    expect(mark).not.toBeNull()
    expect(mark.closest('button')).toBeNull()
  })

  it('is a no-op for an empty query', () => {
    container = makeContainer('some text here')
    expect(applyHighlight(container, '')).toBe(false)
    expect(applyHighlight(container, '   ')).toBe(false)
  })

  it('returns false and does nothing when container is null', () => {
    expect(applyHighlight(null, 'needle')).toBe(false)
    expect(() => clearHighlight(null)).not.toThrow()
  })
})
