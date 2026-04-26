/**
 * Analytics utility helpers: formatting, bucket-size selection, range presets.
 */

/**
 * Select bucket granularity based on time range duration in seconds.
 * < 48 hours → 'hour', >= 48 hours → 'day'
 */
export function selectBucketSize(since, until) {
  const durationHours = (until - since) / 3600
  return durationHours < 48 ? 'hour' : 'day'
}

/** Format a USD cost to a short display string. */
export function formatCost(usd) {
  if (usd == null) return '—'
  if (usd === 0) return '$0.00'
  if (usd < 0.001) return '<$0.001'
  if (usd < 1) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

/** Format a token count with K/M suffix. */
export function formatTokens(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

/** Time range presets: { label, seconds } */
export const TIME_PRESETS = [
  { label: '1h', seconds: 3600 },
  { label: '6h', seconds: 6 * 3600 },
  { label: '24h', seconds: 24 * 3600 },
  { label: '7d', seconds: 7 * 86400 },
  { label: '30d', seconds: 30 * 86400 },
]

/** Return { since, until } Unix seconds for a preset label. */
export function presetToRange(label) {
  const preset = TIME_PRESETS.find(p => p.label === label)
  if (!preset) return null
  const until = Math.floor(Date.now() / 1000)
  return { since: until - preset.seconds, until }
}

/** Format a bucket_ts (Unix seconds) as a readable label. */
export function formatBucketLabel(ts, groupBy) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  if (groupBy === 'hour') {
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
  }
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
