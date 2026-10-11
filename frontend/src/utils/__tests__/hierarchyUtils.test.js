import { describe, it, expect, vi } from 'vitest'
import { walkHierarchy, flattenHierarchy, findInHierarchy, filterHierarchy } from '@/utils/hierarchyUtils'

const SINGLE = { id: 'a', name: 'A', children: [] }

const TREE = {
  id: 'root', name: 'Root', type: 'user',
  children: [
    {
      id: 'b', name: 'B', children: [
        { id: 'd', name: 'D', children: [] },
        { id: 'e', name: 'E', children: [{ id: 'g', name: 'G', children: [] }] },
      ],
    },
    { id: 'c', name: 'C', children: [{ id: 'f', name: 'F', children: [] }] },
  ],
}

describe('walkHierarchy', () => {
  it('does not call visitor for null root', () => {
    const fn = vi.fn()
    walkHierarchy(null, fn)
    expect(fn).not.toHaveBeenCalled()
  })

  it('visits single node at depth 0', () => {
    const visits = []
    walkHierarchy(SINGLE, (node, depth) => visits.push({ id: node.id, depth }))
    expect(visits).toEqual([{ id: 'a', depth: 0 }])
  })

  it('visits all nodes in DFS order with correct depths', () => {
    const visits = []
    walkHierarchy(TREE, (node, depth) => visits.push({ id: node.id, depth }))
    expect(visits).toEqual([
      { id: 'root', depth: 0 },
      { id: 'b', depth: 1 },
      { id: 'd', depth: 2 },
      { id: 'e', depth: 2 },
      { id: 'g', depth: 3 },
      { id: 'c', depth: 1 },
      { id: 'f', depth: 2 },
    ])
  })

  it('prunes subtree when visitor returns false', () => {
    const visits = []
    walkHierarchy(TREE, (node, depth) => {
      visits.push(node.id)
      if (node.id === 'b') return false
    })
    expect(visits).toEqual(['root', 'b', 'c', 'f'])
  })

  it('does not throw for node without children field', () => {
    const node = { id: 'x', name: 'X' }
    expect(() => walkHierarchy(node, () => {})).not.toThrow()
  })
})

describe('flattenHierarchy', () => {
  it('returns empty array for null root', () => {
    expect(flattenHierarchy(null)).toEqual([])
  })

  it('returns single entry for single node', () => {
    const result = flattenHierarchy(SINGLE)
    expect(result).toEqual([{ node: SINGLE, depth: 0 }])
  })

  it('returns 7 entries in DFS order with correct depths', () => {
    const result = flattenHierarchy(TREE)
    expect(result.map(r => ({ id: r.node.id, depth: r.depth }))).toEqual([
      { id: 'root', depth: 0 },
      { id: 'b', depth: 1 },
      { id: 'd', depth: 2 },
      { id: 'e', depth: 2 },
      { id: 'g', depth: 3 },
      { id: 'c', depth: 1 },
      { id: 'f', depth: 2 },
    ])
  })

  it('excludes subtree rooted at excluded node', () => {
    const result = flattenHierarchy(TREE, { exclude: new Set(['b']) })
    expect(result.map(r => r.node.id)).toEqual(['root', 'c', 'f'])
  })

  it('excludes everything when root id is excluded', () => {
    const result = flattenHierarchy(TREE, { exclude: new Set(['root']) })
    expect(result).toEqual([])
  })

  it('behaves same as no options when exclude is undefined', () => {
    const withUndefined = flattenHierarchy(TREE, { exclude: undefined })
    const withoutOptions = flattenHierarchy(TREE)
    expect(withUndefined.map(r => r.node.id)).toEqual(withoutOptions.map(r => r.node.id))
  })
})

describe('findInHierarchy', () => {
  it('returns null for null root', () => {
    expect(findInHierarchy(null, () => true)).toBeNull()
  })

  it('returns root when predicate matches root', () => {
    expect(findInHierarchy(TREE, n => n.id === 'root')).toBe(TREE)
  })

  it('returns null when predicate never matches', () => {
    expect(findInHierarchy(TREE, n => n.id === 'z')).toBeNull()
  })

  it('returns deep node matching predicate', () => {
    const gNode = TREE.children[0].children[1].children[0]
    expect(findInHierarchy(TREE, n => n.id === 'g')).toBe(gNode)
  })

  it('returns first DFS match when multiple nodes match', () => {
    // All nodes have a non-empty id; root is first visited in DFS
    const result = findInHierarchy(TREE, n => n.id.length > 0)
    expect(result.id).toBe('root')
  })

  it('does not throw for node without children field', () => {
    const node = { id: 'x', name: 'X' }
    expect(() => findInHierarchy(node, () => false)).not.toThrow()
  })
})

describe('filterHierarchy', () => {
  it('returns null for null root', () => {
    expect(filterHierarchy(null, () => true)).toBeNull()
  })

  it('keeps the root undimmed when the root itself matches', () => {
    const result = filterHierarchy(TREE, n => n.id === 'root')
    expect(result._filterDimmed).toBe(false)
    // Descendants none of which match are pruned entirely — only root survives.
    expect(result.children).toEqual([])
  })

  it('keeps a dimmed ancestor chain down to a matching deep descendant, prunes unrelated siblings', () => {
    const result = filterHierarchy(TREE, n => n.id === 'd')

    expect(result.id).toBe('root')
    expect(result._filterDimmed).toBe(true)
    expect(result.children).toHaveLength(1)

    const b = result.children[0]
    expect(b.id).toBe('b')
    expect(b._filterDimmed).toBe(true)
    expect(b.children).toHaveLength(1)

    const d = b.children[0]
    expect(d.id).toBe('d')
    expect(d._filterDimmed).toBe(false)
    expect(d.children).toEqual([])

    // 'e' (sibling of 'd') and 'c' (sibling of 'b', with descendant 'f') have no
    // match anywhere in their subtree — pruned entirely.
  })

  it('returns null when nothing in the tree matches', () => {
    expect(filterHierarchy(TREE, () => false)).toBeNull()
  })

  it('keeps every node undimmed when everything matches', () => {
    const result = filterHierarchy(TREE, () => true)
    const ids = []
    walkHierarchy(result, (node) => { ids.push(node.id); expect(node._filterDimmed).toBe(false) })
    expect(ids).toEqual(['root', 'b', 'd', 'e', 'g', 'c', 'f'])
  })

  it('does not mutate the input tree', () => {
    const snapshot = JSON.parse(JSON.stringify(TREE))
    filterHierarchy(TREE, n => n.id === 'd')
    expect(TREE).toEqual(snapshot)
    expect(TREE._filterDimmed).toBeUndefined()
    expect(TREE.children[0]._filterDimmed).toBeUndefined()
  })
})
