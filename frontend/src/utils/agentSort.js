export const SORT_ALPHA = 'alpha'
export const SORT_CREATION = 'creation'

export function compareAgents(mode, a, b, { nameOf, orderOf, idOf }) {
  if (mode === SORT_ALPHA) {
    const cmp = (nameOf(a) || '').localeCompare(nameOf(b) || '', undefined, { numeric: true, sensitivity: 'base' })
    if (cmp !== 0) return cmp
    return (idOf(a) || '').localeCompare(idOf(b) || '')
  }
  // SORT_CREATION
  const oa = orderOf(a) ?? Number.MAX_SAFE_INTEGER
  const ob = orderOf(b) ?? Number.MAX_SAFE_INTEGER
  if (oa !== ob) return oa - ob
  return (idOf(a) || '').localeCompare(idOf(b) || '')
}
