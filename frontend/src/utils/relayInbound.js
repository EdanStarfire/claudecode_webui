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
  digest_failures: ['digest failing', 'A scheduled monitor failed 3 times in a row: ask the agent to run it again, or delete it.'],
  digest_rate_limited_24h: ['rate limited', 'Slack rate limited the scheduled runs; they retry every 5 minutes and give up after 3 tries per slot.'],
  digest_overdue: ['run overdue', 'A scheduled run is overdue: check that the Backend is running and the owner switch is on.'],
  digest_ran_late: ['ran late', 'A missed run was executed once after the Backend came back.'],
  digest_deferred: ['postponed', 'A run was postponed because the session had queued messages.'],
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

// Frozen sentences (the same ones the agent-facing tools state)
export const REPEAT_NOTICE = 'Digests and live monitors on the same scope repeat the same messages.'
export const RUN_NOW_TITLE = 'Run now: collects and delivers immediately; wakes the session only if there is content. Applies immediately.'
export const REPEAT_TWIN_NOTICE = 'A live monitor on the same scope also delivers these messages, so the session receives them twice (once live, once in the digest).'

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

/** `{glyph, icon, text, title}`: what the monitor watches, on one line (Slack labels as plain text).
 *  Scheduled rows carry an `icon` (`clock` digest / `inbox` unread) instead of a text glyph. */
export function monitorLabel(m) {
  const channel = m.channel_label || m.channel || ''
  const digest = m.mode === 'digest'
  const prefix = digest ? 'digest · ' : ''
  let text
  switch (m.kind) {
    case 'monitor_thread': text = `${prefix}thread · ${channel}`; break
    case 'monitor_channel': text = prefix + (channel.startsWith('#') || ID_LIKE.test(channel) ? channel : `#${channel}`); break
    case 'monitor_mentions': text = `${prefix}mentions of you`; break
    case 'monitor_dms': text = `${prefix}direct messages`; break
    case 'monitor_unread': text = 'unread'; break
    default: text = m.kind
  }
  const ids = [m.channel, m.thread_ts].filter(Boolean).join(' · ')
  const icon = m.mode === 'unread' || m.kind === 'monitor_unread' ? 'inbox' : (digest ? 'clock' : '')
  return { glyph: icon ? '' : (KIND_GLYPH[m.kind] || '•'), icon, text, title: ids ? `${text} (${ids})` : text }
}

export const isScheduled = (m) => m?.mode === 'digest' || m?.mode === 'unread'

/** Unread option chips: `DMs`, `#a`, `@mentions only`, `repeat`. */
export function unreadChips(m) {
  const u = m.unread
  if (!u) return []
  const chips = []
  if (u.dms) chips.push({ text: 'DMs', title: u.exclude?.length ? `all direct and group messages except ${u.exclude.length}` : 'all direct and group messages' })
  for (const c of u.channels || []) chips.push({ text: u.labels?.[c] || c, title: `channel ${u.labels?.[c] || c} (${c})` })
  if (u.mentions_only) chips.push({ text: '@mentions only', title: 'only messages that mention you' })
  if (u.repeat) chips.push({ text: 'repeat', title: 'lists a conversation again even when it was reported before' })
  return chips
}

/** Keyword / user filters as chips: at most `max` shown, the rest as `+n`. */
export function filterChips(m, max = 2) {
  const f = m.filters || {}
  const all = m.unread ? unreadChips(m) : [
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

export function monitorsLine(health, nowSec = Date.now() / 1000) {
  const m = health?.monitors
  if (!m || !m.total) return 'none'
  const parts = [`${m.active} active`]
  if (m.paused) parts.push(`${m.paused} paused`)
  if (m.suspended) parts.push(`${m.suspended} suspended`)
  if (m.scheduled) {
    const next = toEpoch(m.next_run)
    parts.push(`${m.scheduled} scheduled${next != null ? ` (${nextRunText(next, nowSec)})` : ''}`)
  }
  return parts.join(' · ')
}

/** `digests 5 delivered · 2 empty · rate limited ×1`; empty when no scheduled run happened. */
export function digestWindowLine(health) {
  const w = health?.window_24h
  if (!w) return ''
  const parts = []
  if (w.digest_delivered || w.digest_empty) parts.push(`digests ${w.digest_delivered || 0} delivered · ${w.digest_empty || 0} empty`)
  if (w.rate_limited_runs) parts.push(`rate limited ×${w.rate_limited_runs}`)
  if (w.late_runs) parts.push(`ran late ×${w.late_runs}`)
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

const OK_OUTCOMES = new Set([null, undefined, '', 'ok', 'delivered', 'empty', 'accepted'])
export const isProblem = (row) => !OK_OUTCOMES.has(row.outcome)

// ── session surface (3d-ii) ──────────────────────────────────────────────

/** Total / tag counts from `relay_summary.monitors` (absent on an older Backend -> zeros). */
export function summaryMonitors(summary) {
  const m = summary?.monitors || {}
  const active = m.active || 0
  const paused = m.paused || 0
  const suspended = m.suspended || 0
  return { active, paused, suspended, scheduled: m.scheduled || 0, total: active + paused + suspended }
}

export const monitorCountText = (total) => `${total} ${total === 1 ? 'monitor' : 'monitors'}`

/** Hover text of the session chip: attachment, policy count, monitor count, attention. */
export function summaryTitle(summary) {
  if (!summary) return ''
  const n = summary.policy_count ?? 0
  const parts = [`Relay attached${summary.handle ? ` as !${summary.handle}` : ''}`, `${n} channel ${n === 1 ? 'policy' : 'policies'}`]
  const { total } = summaryMonitors(summary)
  if (total) parts.push(monitorCountText(total))
  if (summary.attention) {
    const { scheduled, total } = summaryMonitors(summary)
    parts.push(scheduled && scheduled === total ? 'needs attention (monitors) · scheduled monitors' : 'needs attention (monitors)')
  }
  return parts.join(' · ')
}

/** True when the session chip shows `!handle` text: a handle that differs from the session slug (exact). */
export const relayChipShowsText = (summary, slug) => !!summary?.handle && summary.handle !== (slug || '')

// ── scheduled monitors (3c-iii) ──────────────────────────────────────────

const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function daysShort(days) {
  if (!days || days.length === 7) return ''
  if (days.length >= 3 && days.every((d, i) => i === 0 || d === days[i - 1] + 1)) return `${DAY_SHORT[days[0]]}–${DAY_SHORT[days[days.length - 1]]}`
  return days.map(d => DAY_SHORT[d]).join(', ')
}

/** Short schedule for the row line: `daily 09:00, 17:00 Mon–Fri` / `every 2 h`; the server sentence otherwise. */
export function scheduleSummary(m) {
  const s = m?.schedule
  if (!s) return m?.schedule_text || ''
  if (s.type === 'interval') {
    const n = s.minutes
    return n % 60 === 0 ? `every ${n / 60} h` : `every ${n} min`
  }
  const days = daysShort(s.days)
  return `daily ${(s.times || []).join(', ')}${days ? ` ${days}` : ''}`
}

const pad = (n) => String(n).padStart(2, '0')
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`

/** `next run in 3 h` / `next run today 17:00` / `overdue by 12 min`; empty without a slot. */
export function nextRunText(nextRun, nowSec = Date.now() / 1000) {
  const t = toEpoch(nextRun)
  if (t == null) return ''
  const delta = t - nowSec
  if (delta < -60) return `overdue by ${span(-delta)}`
  if (delta < 60) return 'next run in moments'
  if (delta < 6 * 3600) return `next run in ${span(delta)}`
  const when = new Date(t * 1000)
  const now = new Date(nowSec * 1000)
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  if (when.toDateString() === now.toDateString()) return `next run today ${hhmm(when)}`
  if (when.toDateString() === tomorrow.toDateString()) return `next run tomorrow ${hhmm(when)}`
  return `next run ${DAY_SHORT[(when.getDay() + 6) % 7]} ${hhmm(when)}`
}

const countWord = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
const REFUSAL_WORDS = {
  disabled_by_owner: 'inbound is switched off', no_user_token: 'no user token', relay_missing: 'relay missing',
  channel_blocked: 'channel blocked', policy_unset: 'no policy for the channel', paused: 'paused',
  not_active: 'not active', session_inactive: 'session inactive', disposed: 'session disposed',
}

/** What the last run did, in words (`delivered 12 messages · 2 h ago`); empty before the first run. */
export function lastRunText(lastRun, nowSec = Date.now() / 1000, { unread = false } = {}) {
  if (!lastRun) return ''
  const ago = agoText(lastRun.at, nowSec)
  const unit = unread ? 'conversation' : 'message'
  const n = lastRun.messages || 0
  const at = (v) => clockTime(v)
  switch (lastRun.outcome) {
    case 'delivered':
      return `${lastRun.late ? 'ran late after a restart · ' : ''}delivered ${countWord(n, unit)}${lastRun.omitted ? ` (+${lastRun.omitted} omitted)` : ''} · ${ago}`
    case 'empty': return `empty · ${ago} (nothing new, session not woken)`
    case 'rate_limited':
      if (lastRun.code === 'rate_limited_gave_up') return `gave up this slot after ${lastRun.retry || 3} tries · ${ago}`
      if (lastRun.retry_at) return `rate limited · retrying ${at(lastRun.retry_at)} (${lastRun.retry || 1}/3)`
      return `rate limited · ${ago}`
    case 'deferred': return lastRun.retry_at ? `deferred (session busy) · retrying ${at(lastRun.retry_at)}` : 'deferred (session busy)'
    case 'refused': return `refused: ${REFUSAL_WORDS[lastRun.code] || (lastRun.code || 'not run').replace(/_/g, ' ')} · ${ago}`
    case 'failed': return `failed · ${ago}`
    default: return `${lastRun.outcome} · ${ago}`
  }
}

const TRIGGER_WORDS = { schedule: 'schedule', late: 'late (missed slot)', manual: 'run now (you)', agent: 'run now (agent)' }
export const triggerWord = (t) => TRIGGER_WORDS[t] || t || ''

/** Plain sentence for a refused or failed Run now. `status`: HTTP status, `detail`: parsed error detail. */
export function runNowError({ status, code, retryAfter } = {}) {
  const c = code || ''
  if (status === 429 || c === 'run_too_soon') return `Please wait ${Math.max(1, Math.ceil(retryAfter || 1))} s before running it again.`
  if (c === 'monitor_paused') return 'Paused: resume it first.'
  if (c === 'monitor_suspended') return 'Suspended by the system: it cannot run until the cause is resolved.'
  if (c === 'run_in_progress') return 'A run of this monitor is already in progress.'
  if (c === 'inbound_unavailable') return 'Inbound is switched off or has no user token, so scheduled runs are unavailable.'
  if (status === 404) return 'This monitor was already removed.'
  return 'Run now failed. Try again in a moment.'
}

/** Why Run now is disabled for this row, or '' when it can run. */
export function runNowDisabledReason(m) {
  if (m.paused) return 'Paused: resume it first.'
  if (m.status && m.status !== 'active') return `Suspended${m.suspended_reason ? ` (${String(m.suspended_reason).replace(/_/g, ' ')})` : ''}: it runs again once it resumes.`
  return ''
}

/** A live monitor of the same session on the same scope as this digest (repeat notice). */
export function hasLiveTwin(m, rows) {
  if (m.mode !== 'digest') return false
  return (rows || []).some(o => o.id !== m.id && (!o.mode || o.mode === 'live') && o.session_id === m.session_id
    && o.kind === m.kind && (o.channel || null) === (m.channel || null) && (o.thread_ts || null) === (m.thread_ts || null))
}
