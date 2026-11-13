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
  monitor_digest: 'Monitor digest run', // scheduled digest / unread runs (3c); the unread kind has its own label
}

export const MONITOR_KINDS = {
  monitor_thread: 'thread', monitor_channel: 'channel', monitor_mentions: 'mentions', monitor_dms: 'DMs',
  monitor_unread: 'unread',
}

/** Event label; scheduled unread runs say so. */
export const eventLabel = (r) => (r.event === 'monitor_digest' && r.monitor_kind === 'monitor_unread' ? 'Monitor unread run' : EVENTS[r.event])

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
    case 'monitor_digest':
      return digestDetail(r, parts)
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

const DIGEST_REFUSALS = {
  monitor_paused: 'monitor paused', monitor_suspended: 'monitor suspended', run_too_soon: 'too soon',
  run_in_progress: 'already running', inbound_unavailable: 'inbound unavailable', not_a_digest_monitor: 'not a scheduled monitor',
  monitor_not_found: 'monitor not found', disabled_by_owner: 'inbound switched off', no_user_token: 'no user token',
  channel_blocked: 'channel blocked', policy_unset: 'no policy', paused: 'monitor paused',
}
const SOURCE_WORDS = { owner: 'run now by you', agent: 'run now by the agent' }

// A scheduled run: counts and codes only ("12 messages · +3 omitted", "rate limited, retry 2/3 at 14:35").
function digestDetail(r, parts) {
  const unit = r.monitor_kind === 'monitor_unread' ? 'conversation' : 'message'
  switch (r.outcome) {
    case 'delivered':
      parts.push(plural(r.count || 0, unit))
      if (r.omitted) parts.push(`+${r.omitted} omitted`)
      break
    case 'empty': parts.push('nothing new, session not woken'); break
    case 'rate_limited':
      if (r.code === 'rate_limited_gave_up') parts.push('gave up this slot')
      else if (r.code === 'rate_limited_rescheduled') {
        const at = r.retry_at ? new Date(r.retry_at) : null
        const when = at && !Number.isNaN(at.getTime()) ? ` at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''
        parts.push(`rate limited, retry ${r.retry || 1}/3${when}`)
      } else parts.push('rate limited')
      break
    case 'deferred': parts.push(`postponed, session busy${r.retry ? ` (${r.retry}/6)` : ''}`); break
    case 'refused': parts.push(`refused: ${DIGEST_REFUSALS[r.code] || (r.code || '').replace(/_/g, ' ')}`); break
    case 'failed': parts.push(`failed${r.code ? `: ${r.code.replace(/_/g, ' ')}` : ''}`); break
    case 'accepted': parts.push('started'); break
    default: if (r.code) parts.push(r.code)
  }
  if (r.code === 'late' && r.outcome !== 'refused') parts.push('ran late (missed slot)')
  if (SOURCE_WORDS[r.source]) parts.push(SOURCE_WORDS[r.source])
  return parts.join(' · ')
}
