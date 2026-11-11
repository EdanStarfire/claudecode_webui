// Pure helpers for the Library > Relays > Inbound tab (issue #2103, UI stage 3d-i): plain-language
// copy, tags, relative times. No Vue, no I/O. Slack-controlled strings (labels, keywords) pass through
// untouched: components render them with text interpolation only.

export const ATTENTION = 'attention'

export const STATUS_LABELS = {
  connected: 'connected', connecting: 'connecting', backoff: 'reconnecting',
  transport_unavailable: 'unavailable', auth_failed: 'auth failed', disabled: 'off', stopped: 'off',
}

// What each attention / info item says and what to do about it. Frozen copy: keep it plain.
export const ATTENTION_COPY = {
  auth_failed: ['auth failed', 'Slack rejected the app token. Update it in Setup.'],
  transport_unavailable: ['unavailable', 'The Slack connection library is not installed on this Backend.'],
  reconnecting_long: ['reconnecting', 'Still trying to reach Slack. Check the network and the app token.'],
  inbound_off_with_monitors: ['inbound off', 'Inbound is off or missing a token, so monitors receive nothing. Turn it on or add the token in Setup.'],
  monitors_flood: ['flood', 'Monitors stopped after too many messages. Ask the agent to run them again, or delete them.'],
  monitors_delivery_failed: ['delivery failed', 'Monitors stopped after repeated delivery failures. Ask the agent to run them again, or delete them.'],
  monitors_policy_unset: ['no policy', 'A monitored channel has no policy any more. Set one for it and the monitor resumes by itself.'],
  monitor_error: ['delivery errors', 'A monitor failed to deliver recently. It retries by itself; check the activity below.'],
  backfill_gap_24h: ['gap', 'Some messages from a disconnected period could not be fetched again. Details in the audit.'],
  backfill_cap_24h: ['catch-up capped', 'A catch-up hit its limits, so older messages were skipped. Details in the audit.'],
  instruction_delivery_failed_24h: ['instruction failed', 'An instruction from Slack could not be delivered to its session.'],
  monitors_channel_blocked: ['blocked', 'Paused by policy. They resume by themselves when the channel is readable again.'],
  monitors_disposed: ['disposed', 'Their session was disposed. They resume when it is started again.'],
  monitors_session_inactive: ['session inactive', 'Their session cannot receive messages. They resume by themselves when it can.'],
  monitors_paused: ['paused', 'You paused them. Resume to start again from that moment.'],
  monitors_expiring: ['expiring', 'These monitors end within a day.'],
}

export function attentionLabel(item) {
  const [label] = ATTENTION_COPY[item.code] || [item.code]
  return item.count > 1 ? `${label} ×${item.count}` : label
}

export function attentionHint(item) {
  return (ATTENTION_COPY[item.code] || [])[1] || ''
}

/** `/settings/relays` query for the Audit link of an attention item. */
export function auditQuery(item) {
  const a = item.audit || {}
  const query = { tab: 'audit' }
  if (a.events?.length) query.events = a.events.join(',')
  if (a.session_id) query.session = a.session_id
  return query
}

export const hasAttention = (health) => !!health?.attention?.some(a => a.severity === ATTENTION)

// ── monitor state ────────────────────────────────────────────────────────

const SUSPEND_WORDS = {
  channel_blocked: 'channel blocked', policy_unset: 'no policy', flood: 'flood',
  delivery_failed: 'delivery failed', disposed: 'disposed', session_inactive: 'session inactive',
}
const AMBER_REASONS = new Set(['policy_unset', 'flood', 'delivery_failed'])

export const STATE_COPY = {
  channel_blocked: 'Paused by policy: the channel is blocked for this session. It resumes by itself, from that moment on, when the channel is readable again.',
  policy_unset: 'The channel has no policy any more. Set a policy for it (Relay > policies) and it resumes by itself.',
  session_inactive: 'The session is not attached to this relay or cannot receive messages. It resumes by itself when it can.',
  disposed: 'The session was disposed. It resumes when the session is started again.',
  flood: 'Stopped after too many messages in a row. Ask the agent to run the monitor again, or delete it.',
  delivery_failed: 'Stopped after repeated delivery failures. Ask the agent to run the monitor again, or delete it.',
  paused: 'You paused it. Resuming starts from the moment you resume.',
  active: 'Watching now.',
}

/** The state tag: `{label, tone}` with tone `muted` (active), `neutral` or `amber` (needs the owner). */
export function stateTag(m) {
  if (m.effective_state === 'paused') return { label: 'paused', tone: 'neutral' }
  if (m.effective_state === 'suspended') {
    const reason = m.suspended_reason
    return { label: `suspended: ${SUSPEND_WORDS[reason] || reason || 'unknown'}`, tone: AMBER_REASONS.has(reason) ? 'amber' : 'neutral' }
  }
  return { label: 'active', tone: 'muted' }
}

/** Plain explanation + what to do. A paused monitor that is also suspended explains the suspension. */
export function stateCopy(m) {
  if (m.status !== 'active' && m.suspended_reason) {
    const base = STATE_COPY[m.suspended_reason] || 'Suspended.'
    return m.paused ? `${base} It is also paused by you.` : base
  }
  return m.paused ? STATE_COPY.paused : STATE_COPY.active
}

// ── monitor row ──────────────────────────────────────────────────────────

const ID_LIKE = /^[CGDW][A-Z0-9]{6,}$/ // an unresolved Slack id is shown as is
const KIND_GLYPH = { monitor_thread: '↳', monitor_channel: '#', monitor_mentions: '@', monitor_dms: '✉\uFE0E' }

/** `{glyph, text, title}`: what the monitor watches, on one line (Slack labels as plain text). */
export function monitorLabel(m) {
  const channel = m.channel_label || m.channel || ''
  let text
  switch (m.kind) {
    case 'monitor_thread': text = `thread · ${channel}`; break
    case 'monitor_channel': text = channel.startsWith('#') || ID_LIKE.test(channel) ? channel : `#${channel}`; break
    case 'monitor_mentions': text = 'mentions of you'; break
    case 'monitor_dms': text = 'direct messages'; break
    default: text = m.kind
  }
  const ids = [m.channel, m.thread_ts].filter(Boolean).join(' · ')
  return { glyph: KIND_GLYPH[m.kind] || '•', text, title: ids ? `${text} (${ids})` : text }
}

/** Keyword / user filters as chips: at most `max` shown, the rest as `+n`. */
export function filterChips(m, max = 2) {
  const f = m.filters || {}
  const all = [
    ...(f.keywords || []).map(k => ({ text: `kw: ${k}`, title: `keyword: ${k}` })),
    ...(f.users || []).map(u => {
      const label = f.user_labels?.[u] || u
      return { text: label.startsWith('@') ? label : `@${label}`, title: `from ${label} (${u})` }
    }),
  ]
  return { chips: all.slice(0, max), more: Math.max(0, all.length - max), all }
}

// ── times ────────────────────────────────────────────────────────────────

/** Epoch seconds from a number, a numeric string (Slack ts) or an ISO string; null when unknown. */
export function toEpoch(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number') return value
  if (/^\d+(\.\d+)?$/.test(value)) return Number(value)
  const t = Date.parse(value)
  return Number.isNaN(t) ? null : t / 1000
}

function span(seconds) {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return 'moments'
  if (s < 3600) return `${Math.floor(s / 60)} min`
  if (s < 86400) return `${Math.floor(s / 3600)} h`
  return `${Math.floor(s / 86400)} d`
}

export function agoText(value, nowSec = Date.now() / 1000) {
  const t = toEpoch(value)
  if (t == null) return ''
  const d = span(nowSec - t)
  return d === 'moments' ? 'just now' : `${d} ago`
}

export function sinceText(value, nowSec = Date.now() / 1000) {
  const t = toEpoch(value)
  return t == null ? '' : `for ${span(nowSec - t)}`
}

export function clockTime(value) {
  const t = toEpoch(value)
  return t == null ? '' : new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function fullTime(value) {
  const t = toEpoch(value)
  return t == null ? '' : new Date(t * 1000).toLocaleString()
}

export const caughtUpTo = (lastSeenTs, nowSec) => {
  const t = toEpoch(lastSeenTs)
  return t == null ? '' : `caught up to ${agoText(t, nowSec)}`
}

/** `expires in 2 d` / `expires in 5 h`; null without an expiry or once past it. */
export function expiresText(expiresAt, nowSec = Date.now() / 1000) {
  const t = toEpoch(expiresAt)
  if (t == null) return null
  if (t <= nowSec) return 'expired'
  return `expires in ${span(t - nowSec)}`
}

export const deliveredText = (m, nowSec) => (m.last_delivered_at ? `delivered ${agoText(m.last_delivered_at, nowSec)}` : 'nothing delivered yet')

// ── health lines ─────────────────────────────────────────────────────────

export function monitorsLine(health) {
  const m = health?.monitors
  if (!m || !m.total) return 'none'
  const parts = [`${m.active} active`]
  if (m.paused) parts.push(`${m.paused} paused`)
  if (m.suspended) parts.push(`${m.suspended} suspended`)
  return parts.join(' · ')
}

export function windowLine(health) {
  const w = health?.window_24h
  if (!w) return ''
  const atLeast = w.truncated ? 'at least ' : ''
  const i = w.instructions || {}
  const instr = `${i.delivered || 0} ok${i.refused ? ` / ${i.refused} refused` : ''}${i.late ? ` / ${i.late} late` : ''}${i.rate_limited ? ` / ${i.rate_limited} limited` : ''}${i.delivery_failed ? ` / ${i.delivery_failed} failed` : ''}`
  return `${atLeast}delivered ${w.delivered_messages} msgs · dropped ${w.dropped_messages} · flood ${w.flood_events} · gaps ${w.gap_events} · caps ${w.cap_events} · instructions ${instr}`
}

// ── activity feed ────────────────────────────────────────────────────────

export const ACTIVITY_CHIPS = [
  { key: 'all', label: 'All', events: 'inbound_*,monitor_*' },
  { key: 'instructions', label: 'Instructions', events: 'inbound_instruction,inbound_ack,inbound_reply,inbound_reply_failed' },
  { key: 'monitors', label: 'Monitors', events: 'monitor_*' },
  { key: 'connection', label: 'Connection', events: 'inbound_connection' },
  { key: 'problems', label: 'Problems', events: 'inbound_*,monitor_*', problems: true },
]

const OK_OUTCOMES = new Set([null, undefined, '', 'ok', 'delivered'])
export const isProblem = (row) => !OK_OUTCOMES.has(row.outcome)

// ── session surface (3d-ii) ──────────────────────────────────────────────

/** Total / tag counts from `relay_summary.monitors` (absent on an older Backend -> zeros). */
export function summaryMonitors(summary) {
  const m = summary?.monitors || {}
  const active = m.active || 0
  const paused = m.paused || 0
  const suspended = m.suspended || 0
  return { active, paused, suspended, total: active + paused + suspended }
}

export const monitorCountText = (total) => `${total} ${total === 1 ? 'monitor' : 'monitors'}`

/** Hover text of the session chip: attachment, policy count, monitor count, attention. */
export function summaryTitle(summary) {
  if (!summary) return ''
  const n = summary.policy_count ?? 0
  const parts = [`Relay attached${summary.handle ? ` as !${summary.handle}` : ''}`, `${n} channel ${n === 1 ? 'policy' : 'policies'}`]
  const { total } = summaryMonitors(summary)
  if (total) parts.push(monitorCountText(total))
  if (summary.attention) parts.push('needs attention (monitors)')
  return parts.join(' · ')
}

/** True when the session chip shows `!handle` text: a handle that differs from the session slug (exact). */
export const relayChipShowsText = (summary, slug) => !!summary?.handle && summary.handle !== (slug || '')
