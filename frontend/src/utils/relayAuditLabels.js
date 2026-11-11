// Humanized labels for relay audit rows (shared by the Audit tab and the Inbound activity feed).
// Only ids, counts and reason codes are ever shown here: the audit holds no message text for
// inbound / monitor events.

export const EVENTS = {
  inbound_instruction: 'Slack instruction',
  inbound_reply: 'Slack reply',
  inbound_reply_failed: 'Slack reply',
  inbound_ack: 'Slack acknowledgement', // received reaction on a dispatched instruction (#2364)
  inbound_connection: 'Inbound connection',
  inbound_drop_summary: 'Inbound drops',
  inbound_housekeeping: 'Inbound housekeeping',
  // Monitors (stage 3b-i): never any message text, only ids, counts and reasons
  monitor_created: 'Monitor created',
  monitor_removed: 'Monitor removed',
  monitor_suspended: 'Monitor suspended',
  monitor_resumed: 'Monitor resumed',
  monitor_paused: 'Monitor paused',
  monitor_delivered: 'Monitor delivered',
  monitor_delivery_failed: 'Monitor delivery failed',
  monitor_expired: 'Monitor expired',
  monitor_flood: 'Monitor flood',
  monitor_backfill: 'Monitor backfill',
}

export const MONITOR_KINDS = {
  monitor_thread: 'thread', monitor_channel: 'channel', monitor_mentions: 'mentions', monitor_dms: 'DMs',
}

export function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

const reasonList = (counts) => Object.entries(counts || {}).map(([k, n]) => `${k} ${n}`).join(' · ')

/** Humanized detail for an inbound_* / monitor_* row (empty string for other events). */
export function inboundDetail(r) {
  if (r.event === 'inbound_connection') return r.inbound_status || ''
  if (r.event === 'inbound_drop_summary') return reasonList(r.reason_counts)
  if (r.event === 'inbound_housekeeping') return r.count != null ? `${r.count}` : ''
  if (r.event?.startsWith('monitor_')) return monitorDetail(r)
  return ''
}

// Humanized monitor rows: kind + count or reason (no message text is ever audited).
export function monitorDetail(r) {
  const parts = []
  const kind = MONITOR_KINDS[r.monitor_kind]
  if (kind) parts.push(`${kind} monitor`)
  switch (r.event) {
    case 'monitor_created':
    case 'monitor_resumed':
    case 'monitor_paused':
      if (r.count) parts.push(plural(r.count, 'keyword'))
      if (r.approval === 'prompt_allow') parts.push('owner consented')
      if (r.source) parts.push(`by ${r.source}`)
      break
    case 'monitor_removed':
      if (r.source) parts.push(`by ${r.source}`)
      if (r.count) parts.push(plural(r.count, 'monitor'))
      break
    case 'monitor_delivered':
      if (r.count != null) parts.push(plural(r.count, 'message'))
      if (r.omitted) parts.push(`+${r.omitted} omitted`)
      break
    case 'monitor_flood':
      parts.push('over the flood limit, messages dropped')
      break
    case 'monitor_backfill':
      if (r.code === 'gap_notice') parts.push('backfill incomplete, gap notice sent with the next envelope')
      else if (r.code === 'backfill_truncated') parts.push('older content omitted')
      if (r.count != null && r.code !== 'gap_notice') parts.push(plural(r.count, 'message'))
      if (r.reason_counts) parts.push(reasonList(r.reason_counts))
      break
    default:
      if (r.code) parts.push(r.code)
  }
  return parts.join(' · ')
}
