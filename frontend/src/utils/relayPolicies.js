/**
 * Relay channel policy helpers (issue #2103, UI stage 2b).
 *
 * Stored shape is nested: { relay_id: { target: policy } }. A legacy flat dict
 * ({ target: policy }) is read the way the backend reads it: wrapped under the sole relay,
 * else dropped. The UI only ever WRITES the nested shape.
 */
export function normalizePolicies(value, soleRelayId) {
  if (!value || typeof value !== 'object') return {}
  const entries = Object.entries(value)
  if (!entries.length) return {}
  const nested = entries.every(([, v]) => v && typeof v === 'object')
  if (nested) return value
  return soleRelayId ? { [soleRelayId]: Object.fromEntries(entries.filter(([, v]) => typeof v === 'string')) } : {}
}
