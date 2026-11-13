// Shared fixtures for the Inbound tab tests (issue #2103 3d-i).
export const NOW = 1_800_000_000

export const monitor = (over = {}) => ({
  id: 'm1', kind: 'monitor_channel', session_id: 's1', session_name: 'Deploy agent', handle: 'deploy',
  created_by: 's1', created_at: NOW - 86400, expires_at: null, status: 'active', suspended_reason: null,
  paused: false, effective_state: 'active', channel: 'C0AAAAAAA1', channel_label: '#ops', thread_ts: null,
  filters: { keywords: [], users: [], user_labels: {} }, consent: null, last_delivered_at: NOW - 300,
  last_error: null, counters: { delivered: 41, dropped: 3, coalesced: 2 }, last_seen_ts: String(NOW - 120),
  last_activity_at: NOW - 300, failures: 0, flood_windows: 0, ...over,
})

export const health = (over = {}) => ({
  relay_id: 'r1', enabled: true, status: 'connected', detail: null, status_since: new Date((NOW - 3600) * 1000).toISOString(),
  connected_at: NOW - 3600, inflight: 0,
  monitors: { total: 3, active: 2, paused: 1, suspended: 0, by_reason: {} },
  last_backfill: null, last_backfill_audit: null,
  window_24h: {
    since: '', truncated: false, rows_read: 10, delivered_envelopes: 4, delivered_messages: 41, dropped_messages: 3,
    flood_events: 0, delivery_failed: 0, gap_events: 1, cap_events: 0,
    instructions: { delivered: 5, refused: 1, late: 0, rate_limited: 0, delivery_failed: 0 }, connection_changes: 2,
  },
  attention: [], ...over,
})

// Scheduled monitors (3c-iii)
export const digest = (over = {}) => monitor({
  id: 'd1', mode: 'digest', schedule: { type: 'daily', times: ['09:00', '17:00'], days: [0, 1, 2, 3, 4], tz: 'Europe/Paris', tz_source: 'slack' },
  schedule_text: 'daily at 09:00 and 17:00, Mon–Fri, Europe/Paris (your Slack timezone)', tz: 'Europe/Paris', tz_source: 'slack',
  next_run: NOW + 3 * 3600 + 120, last_run_at: NOW - 7200,
  last_run: { at: NOW - 7200, outcome: 'delivered', messages: 12, omitted: 0, gap: false, late: false, trigger: 'schedule' },
  last_run_trigger: 'schedule', run_failures: 0, unread: null, unread_text: null, notified_count: 0, ...over,
})

export const unreadMonitor = (over = {}) => digest({
  id: 'u1', kind: 'monitor_unread', mode: 'unread', channel: null, channel_label: null,
  schedule: { type: 'interval', minutes: 120, anchor: 0 }, schedule_text: 'every 2 hours', tz: null, tz_source: null,
  unread: { dms: true, channels: ['C0AAAAAAA2'], exclude: ['D0X', 'D0Y'], mentions_only: true, repeat: false, labels: { C0AAAAAAA2: '#alpha' } },
  unread_text: 'all direct and group messages except 2, channels #alpha, only messages that mention you, only newly unread',
  notified_count: 1, ...over,
})
