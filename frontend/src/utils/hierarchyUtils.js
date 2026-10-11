/**
 * Depth-first traversal. Calls visitor(node, depth) for each node.
 * Return false from the visitor to skip descending into that node's children.
 */
export function walkHierarchy(root, visitor, depth = 0) {
  if (!root) return
  const skipChildren = visitor(root, depth) === false
  if (skipChildren) return
  for (const child of root.children || []) {
    walkHierarchy(child, visitor, depth + 1)
  }
}

/**
 * Returns flat array of { node, depth } in DFS order.
 * `exclude` (optional Set of ids): when matched, the node AND its subtree
 * are pruned from the result.
 */
export function flattenHierarchy(root, { exclude } = {}) {
  const result = []
  walkHierarchy(root, (node, depth) => {
    if (exclude && exclude.has(node.id)) return false
    result.push({ node, depth })
  })
  return result
}

/**
 * Depth-first first-match. Returns the first node where predicate(node) is
 * truthy, or null if none match.
 */
export function findInHierarchy(root, predicate) {
  if (!root) return null
  if (predicate(root)) return root
  for (const child of root.children || []) {
    const found = findInHierarchy(child, predicate)
    if (found) return found
  }
  return null
}

/**
 * Returns a new tree where nodes failing `predicate` are pruned UNLESS they have at
 * least one descendant that passes — those are kept (for ancestor context) but
 * annotated `_filterDimmed: true`. Nodes passing `predicate` are kept with
 * `_filterDimmed: false`. Returns null if nothing in the tree (including root) passes.
 * Does not mutate the input tree.
 */
export function filterHierarchy(root, predicate) {
  if (!root) return null

  const filteredChildren = (root.children || [])
    .map(child => filterHierarchy(child, predicate))
    .filter(Boolean)

  const selfMatches = predicate(root)
  if (!selfMatches && filteredChildren.length === 0) return null

  return { ...root, children: filteredChildren, _filterDimmed: !selfMatches }
}
