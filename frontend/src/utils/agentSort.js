export const SORT_ALPHA = 'alpha'
export const SORT_CREATION = 'creation'
export const SORT_LAST_ACTIVE = 'last_active'

export function normalizeLastActive(value) {
  if (value == null) return null
  if (typeof value === 'number') {
    return value < 32503680000 ? value * 1000 : value
  }
  const ms = new Date(value).getTime()
  return Number.isNaN(ms) ? null : ms
}

export function compareAgents(mode, a, b, { nameOf, orderOf, idOf, lastActiveOf }, direction = 1) {
  let primary
  if (mode === SORT_ALPHA) {
    primary = (nameOf(a) || '').localeCompare(nameOf(b) || '', undefined, { numeric: true, sensitivity: 'base' })
  } else if (mode === SORT_LAST_ACTIVE) {
    const la = lastActiveOf?.(a) ?? -Infinity
    const lb = lastActiveOf?.(b) ?? -Infinity
    primary = lb - la   // DESC: most recent completion first; -Infinity sorts last
  } else {
    // SORT_CREATION
    const oa = orderOf(a) ?? Number.MAX_SAFE_INTEGER
    const ob = orderOf(b) ?? Number.MAX_SAFE_INTEGER
    primary = oa - ob
  }
  if (primary !== 0) return direction * primary
  return (idOf(a) || '').localeCompare(idOf(b) || '')
}
