<template>
  <div class="mon-row" :class="{ open: expanded, busy }" data-testid="monitor-row" :data-monitor-id="monitor.id">
    <div class="mon-line">
      <button
        type="button"
        class="mon-expand"
        :aria-expanded="expanded"
        :title="expanded ? 'Hide details' : 'Show details'"
        aria-label="Toggle details"
        data-testid="monitor-expand"
        @click="expanded = !expanded"
      >{{ expanded ? '▾' : '▸' }}</button>
      <span v-if="label.icon" class="mon-glyph mon-glyph--icon" :data-icon="label.icon" aria-hidden="true" data-testid="monitor-icon">
        <svg v-if="label.icon === 'clock'" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6" /><path d="M8 4.5V8l2.5 1.5" /></svg>
        <svg v-else viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 9.5l2-6h8l2 6v3.5H2zM2 9.5h3.5l.5 1.5h4l.5-1.5H14" /></svg>
      </span>
      <span v-else class="mon-glyph" aria-hidden="true">{{ label.glyph }}</span>
      <span class="mon-label" :title="label.title" data-testid="monitor-label">{{ label.text }}</span>
      <span v-if="chips.chips.length" class="mon-chips" data-testid="monitor-chips">
        <RelayTag v-for="(c, i) in chips.chips" :key="`${i}:${c.text}`" :title="c.title">{{ c.text }}</RelayTag>
        <RelayTag v-if="chips.more" :title="moreTitle">+{{ chips.more }}</RelayTag>
      </span>
      <RelayTag :tone="tag.tone" :title="stateCopyText" data-testid="monitor-state">{{ tag.label }}</RelayTag>
      <RelayTag v-if="expires" tone="muted" title="The monitor ends then" data-testid="monitor-expires">{{ expires }}</RelayTag>
      <span v-if="showSession" class="mon-session" :title="sessionTitle" data-testid="monitor-session">{{ sessionText }}</span>
      <span class="mon-counts" :title="countsTitle" data-testid="monitor-counts">{{ delivered }} ✓ {{ dropped }} ✕</span>
      <span class="mon-when" :title="fullTime(monitor.last_delivered_at)" data-testid="monitor-delivered">{{ deliveredText(monitor, nowSec) }}</span>
      <span class="mon-actions">
        <template v-if="!confirming">
          <button
            v-if="scheduled"
            type="button"
            class="mon-btn"
            :disabled="busy || running || !!runBlocked"
            :title="runBlocked || RUN_NOW_TITLE"
            aria-label="Run now"
            data-icon="run"
            data-testid="monitor-run"
            @click="$emit('run', monitor)"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3" />
            </svg>
          </button>
          <button
            type="button"
            class="mon-btn"
            :disabled="busy"
            :title="paused ? RESUME_TITLE : PAUSE_TITLE"
            :aria-label="paused ? 'Resume monitor' : 'Pause monitor'"
            :data-icon="paused ? 'play' : 'pause'"
            data-testid="monitor-toggle"
            @click="$emit('toggle-pause', monitor)"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
              <path v-if="paused" d="M4.5 3v10l8-5z" />
              <path v-else d="M4 3h3v10H4zM9 3h3v10H9z" />
            </svg>
          </button>
          <button
            type="button"
            class="mon-btn"
            :disabled="busy"
            :title="DELETE_TITLE"
            aria-label="Delete monitor"
            data-testid="monitor-delete"
            @click="confirming = true"
          >
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M7 7v4M9 7v4" />
            </svg>
          </button>
        </template>
        <template v-else>
          <span class="mon-confirm" :title="scheduled ? DELETE_SCHEDULED_TITLE : DELETE_TITLE" data-testid="monitor-confirm">Delete?<template v-if="scheduled"> Stops the schedule and removes queued content.</template></span>
          <button type="button" class="mon-btn mon-btn--text" :disabled="busy" data-testid="monitor-delete-yes" @click="confirmDelete">yes</button>
          <button type="button" class="mon-btn mon-btn--text" data-testid="monitor-delete-no" @click="confirming = false">no</button>
        </template>
      </span>
    </div>

    <div v-if="scheduled" class="mon-sched" :title="schedTitle" data-testid="monitor-sched">
      <span data-testid="sched-schedule">{{ scheduleShort }}</span>
      <span v-if="nextText" :class="{ 'sched-overdue': overdue }" data-testid="sched-next">· {{ nextText }}</span>
      <span v-if="running" data-testid="sched-running">· Running…</span>
      <span v-else-if="lastText" data-testid="sched-last">· last: {{ lastText }}</span>
      <span v-else data-testid="sched-last">· not run yet</span>
    </div>
    <div v-if="note" class="mon-note" data-testid="monitor-note">{{ note }}</div>

    <div v-if="error" class="mon-error" role="alert" data-testid="monitor-error">{{ error }}</div>

    <div v-if="expanded" class="mon-detail" data-testid="monitor-detail">
      <dl>
        <dt>Watching</dt>
        <dd data-testid="detail-watching">
          {{ kindWord }}<template v-if="!unread">{{ ' ' }}<span :title="label.title">{{ label.text }}</span></template>
          <template v-if="chips.all.length">
            · <span v-for="(c, i) in chips.all" :key="`${i}:${c.text}`" class="detail-chip" :title="c.title">{{ c.text }}</span>
          </template>
          <div v-if="monitor.consent" class="muted" data-testid="detail-consent">
            You allowed this on {{ fullTime(monitor.consent.at) }}{{ monitor.consent.via ? ` (${monitor.consent.via})` : '' }}.
          </div>
          <div class="muted">Created {{ fullTime(monitor.created_at) }}<template v-if="monitor.expires_at"> · ends {{ fullTime(monitor.expires_at) }}</template></div>
        </dd>
        <dt>State</dt>
        <dd data-testid="detail-state">{{ stateCopyText }}</dd>
        <template v-if="scheduled">
          <dt>Schedule</dt>
          <dd data-testid="detail-schedule">
            {{ monitor.schedule_text || scheduleShort }}
            <div v-if="monitor.tz_source === 'utc_fallback'" class="muted" data-testid="detail-tz-fallback">Slack did not report your timezone, so UTC is used.</div>
            <div v-if="monitor.tz" class="muted" data-testid="detail-tz">Timezone {{ monitor.tz }}{{ tzSourceWord }}; set when the monitor was created.</div>
            <div v-if="monitor.next_run" class="muted" :title="fullTime(monitor.next_run)" data-testid="detail-next">{{ nextText }} ({{ fullTime(monitor.next_run) }})</div>
          </dd>
          <dt>Window</dt>
          <dd data-testid="detail-window">
            <template v-if="unread">Slack's own unread state right now. <span v-if="monitor.notified_count" class="muted">{{ monitor.notified_count }} conversations already reported.</span></template>
            <template v-else>Covers everything since it last caught up{{ caught ? ` (${caught.replace('caught up to ', '')})` : '' }}, at most 72 h.</template>
          </dd>
          <dt>Last run</dt>
          <dd data-testid="detail-last-run">
            <template v-if="monitor.last_run">
              {{ lastText }} · <span :title="fullTime(monitor.last_run.at)">{{ fullTime(monitor.last_run.at) }}</span>
              <span class="muted"> · {{ triggerWord(monitor.last_run.trigger || monitor.last_run_trigger) }}</span>
              <div v-if="monitor.last_run.gap" class="muted" data-testid="detail-gap">Incomplete: some items could not be checked (see the audit).</div>
            </template>
            <span v-else class="muted">not run yet</span>
            <div v-if="monitor.run_failures > 0" class="muted" data-testid="detail-run-failures">
              {{ monitor.run_failures }} failed {{ monitor.run_failures === 1 ? 'run' : 'runs' }} in a row
            </div>
          </dd>
          <template v-if="unread && monitor.unread_text">
            <dt>Includes</dt>
            <dd data-testid="detail-unread">{{ monitor.unread_text }}</dd>
          </template>
          <template v-if="twin">
            <dt>Repeats</dt>
            <dd data-testid="detail-repeat">{{ REPEAT_TWIN_NOTICE }}</dd>
          </template>
        </template>
        <dt>Progress</dt>
        <dd data-testid="detail-progress">
          <span v-if="caught">{{ caught }}</span><span v-else>not caught up yet</span>
          · {{ deliveredText(monitor, nowSec) }}
          · {{ delivered }} delivered, {{ dropped }} dropped<template v-if="coalesced">, {{ coalesced }} coalesced</template>
          <div v-if="monitor.failures > 0" class="muted" data-testid="detail-failures">
            {{ monitor.failures }} failed {{ monitor.failures === 1 ? 'delivery' : 'deliveries' }} in a row<template v-if="monitor.last_error">
              · <span :title="`last error: ${monitor.last_error}`">{{ monitor.last_error }}</span></template>
          </div>
        </dd>
        <dt>Session</dt>
        <dd data-testid="detail-session">
          <router-link v-if="monitor.session_id" :to="`/session/${monitor.session_id}`">{{ sessionText }}</router-link>
          <span v-else class="muted">unknown</span>
        </dd>
      </dl>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import RelayTag from './RelayTag.vue'
import {
  REPEAT_TWIN_NOTICE, RUN_NOW_TITLE, caughtUpTo, deliveredText, expiresText, filterChips, fullTime, hasLiveTwin,
  isScheduled, lastRunText, monitorLabel, nextRunText, runNowDisabledReason, scheduleSummary, stateCopy, stateTag,
  triggerWord,
} from '@/utils/relayInbound'

const props = defineProps({
  monitor: { type: Object, required: true },
  nowSec: { type: Number, default: () => Date.now() / 1000 },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  showSession: { type: Boolean, default: true },
  running: { type: Boolean, default: false }, // a Run now is in flight (optimistic "Running…")
  note: { type: String, default: '' }, // one plain info line (e.g. "Started: the result appears in last run")
  peers: { type: Array, default: () => [] }, // the loaded rows: a live twin of a digest triggers the repeat notice
})
const emit = defineEmits(['toggle-pause', 'delete', 'run'])

const PAUSE_TITLE = 'Pause: stop delivering now. Resuming starts from the moment you resume; messages in between are not delivered. Applies immediately.'
const RESUME_TITLE = 'Resume: start again from now. A monitor suspended by the system stays suspended. Applies immediately.'
const DELETE_TITLE = 'Delete: stops it and removes queued, not yet delivered content. The agent can create it again. Applies immediately.'
const DELETE_SCHEDULED_TITLE = 'Delete: stops the schedule and removes queued, not yet delivered content. Applies immediately.'
const KINDS = {
  monitor_thread: 'Thread', monitor_channel: 'Channel', monitor_mentions: 'Mentions', monitor_dms: 'Direct messages',
  monitor_unread: 'Unread',
}
const TZ_SOURCES = { slack: ' (from your Slack profile)', explicit: ' (chosen by the agent)', utc_fallback: '' }

const expanded = ref(false)
const confirming = ref(false)

const label = computed(() => monitorLabel(props.monitor))
const chips = computed(() => filterChips(props.monitor))
const moreTitle = computed(() => chips.value.all.slice(chips.value.chips.length).map(c => c.title).join('\n'))
const tag = computed(() => stateTag(props.monitor))
const stateCopyText = computed(() => stateCopy(props.monitor))
const paused = computed(() => !!props.monitor.paused)
const expires = computed(() => expiresText(props.monitor.expires_at, props.nowSec))
const kindWord = computed(() => KINDS[props.monitor.kind] || props.monitor.kind)
const counters = computed(() => props.monitor.counters || {})
const delivered = computed(() => counters.value.delivered || 0)
const dropped = computed(() => counters.value.dropped || 0)
const coalesced = computed(() => counters.value.coalesced || 0)
const countsTitle = computed(() => `${delivered.value} delivered, ${dropped.value} dropped${coalesced.value ? `, ${coalesced.value} coalesced` : ''} (lifetime)`)
const scheduled = computed(() => isScheduled(props.monitor))
const unread = computed(() => props.monitor.mode === 'unread')
const scheduleShort = computed(() => scheduleSummary(props.monitor))
const nextText = computed(() => (props.monitor.paused || props.monitor.status !== 'active' ? '' : nextRunText(props.monitor.next_run, props.nowSec)))
const overdue = computed(() => nextText.value.startsWith('overdue'))
const lastText = computed(() => lastRunText(props.monitor.last_run, props.nowSec, { unread: unread.value }))
const schedTitle = computed(() => [props.monitor.schedule_text, props.monitor.next_run ? `next run ${fullTime(props.monitor.next_run)}` : '', lastText.value && `last: ${lastText.value}`].filter(Boolean).join('\n'))
const runBlocked = computed(() => runNowDisabledReason(props.monitor))
const tzSourceWord = computed(() => TZ_SOURCES[props.monitor.tz_source] || '')
const twin = computed(() => hasLiveTwin(props.monitor, props.peers))
const caught = computed(() => caughtUpTo(props.monitor.last_seen_ts, props.nowSec))
const sessionText = computed(() => {
  const m = props.monitor
  const name = m.session_name || m.session_id || 'unknown session'
  return m.handle ? `${name} !${m.handle}` : name
})
const sessionTitle = computed(() => sessionText.value)

function confirmDelete() {
  confirming.value = false
  emit('delete', props.monitor)
}
</script>

<style scoped>
.mon-row { border-bottom: 1px solid var(--bs-border-color); padding: 4px 0; font-size: 12px; }
.mon-row.busy { opacity: 0.7; }
.mon-line { display: flex; align-items: center; gap: 6px; min-width: 0; }
.mon-expand, .mon-btn {
  background: none; border: 0; padding: 0 4px; color: var(--bs-secondary-color); cursor: pointer; line-height: 20px; flex: 0 0 auto;
}
.mon-btn { color: var(--bs-tertiary-color, #9ca3af); display: inline-flex; align-items: center; justify-content: center; }
.mon-btn:hover:not(:disabled), .mon-btn:focus-visible { color: var(--bs-body-color); }
.mon-btn:disabled { opacity: 0.4; cursor: default; }
.mon-btn--text { font-size: 11px; text-decoration: underline; }
.mon-glyph { flex: 0 0 auto; color: var(--bs-secondary-color); }
.mon-glyph--icon { display: inline-flex; align-items: center; }
.mon-sched { padding: 1px 0 0 26px; color: var(--bs-secondary-color); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mon-sched > span + span { margin-left: 4px; }
.sched-overdue { color: var(--bs-warning); }
.mon-note { padding: 1px 0 0 26px; color: var(--bs-secondary-color); font-size: 11px; }
.mon-label { flex: 0 1 220px; min-width: 60px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mon-chips { display: inline-flex; gap: 4px; flex: 0 0 auto; }
.mon-session { flex: 1 1 120px; min-width: 40px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--bs-secondary-color); }
.mon-counts, .mon-when { flex: 0 0 auto; color: var(--bs-secondary-color); white-space: nowrap; }
.mon-actions { margin-left: auto; display: inline-flex; align-items: center; gap: 2px; flex: 0 0 auto; }
.mon-confirm { color: var(--bs-secondary-color); }
.mon-error { color: var(--tool-error-text); font-size: 11px; padding: 2px 0 0 26px; }
.mon-detail { padding: 6px 0 4px 26px; color: var(--bs-body-color); }
.mon-detail dl { display: grid; grid-template-columns: 80px 1fr; gap: 4px 10px; margin: 0; }
.mon-detail dt { color: var(--bs-secondary-color); font-weight: 500; }
.mon-detail dd { margin: 0; word-break: break-word; }
.detail-chip { display: inline-block; margin-right: 4px; padding: 0 5px; border: 1px solid var(--bs-border-color); border-radius: 3px; font-size: 10px; }
.muted { color: var(--bs-secondary-color); }
@media (max-width: 600px) {
  /* one compact line (arrow, icon, label, state, actions); chips and counters live in the detail */
  .mon-line { flex-wrap: wrap; row-gap: 2px; }
  .mon-label { flex: 1 1 60px; }
  .mon-chips, .mon-counts, .mon-when { display: none; }
  .mon-session { flex: 1 1 100%; order: 10; padding-left: 26px; }
  .mon-sched { white-space: normal; padding-left: 8px; }
  .mon-note { padding-left: 8px; }
  .mon-btn, .mon-expand { min-width: 32px; min-height: 32px; font-size: 14px; }
  .mon-detail { padding-left: 8px; }
  .mon-detail dl { grid-template-columns: 1fr; gap: 0; }
  .mon-detail dt { margin-top: 4px; }
}
</style>
