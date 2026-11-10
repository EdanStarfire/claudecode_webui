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
      <span class="mon-glyph" aria-hidden="true">{{ label.glyph }}</span>
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
            type="button"
            class="mon-btn"
            :disabled="busy"
            :title="paused ? RESUME_TITLE : PAUSE_TITLE"
            :aria-label="paused ? 'Resume monitor' : 'Pause monitor'"
            data-testid="monitor-toggle"
            @click="$emit('toggle-pause', monitor)"
          >{{ paused ? '▶' : '⏸' }}</button>
          <button
            type="button"
            class="mon-btn"
            :disabled="busy"
            :title="DELETE_TITLE"
            aria-label="Delete monitor"
            data-testid="monitor-delete"
            @click="confirming = true"
          >🗑</button>
        </template>
        <template v-else>
          <span class="mon-confirm" data-testid="monitor-confirm">Delete?</span>
          <button type="button" class="mon-btn mon-btn--text" :disabled="busy" data-testid="monitor-delete-yes" @click="confirmDelete">yes</button>
          <button type="button" class="mon-btn mon-btn--text" data-testid="monitor-delete-no" @click="confirming = false">no</button>
        </template>
      </span>
    </div>

    <div v-if="error" class="mon-error" role="alert" data-testid="monitor-error">{{ error }}</div>

    <div v-if="expanded" class="mon-detail" data-testid="monitor-detail">
      <dl>
        <dt>Watching</dt>
        <dd data-testid="detail-watching">
          {{ kindWord }} <span :title="label.title">{{ label.text }}</span>
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
  caughtUpTo, deliveredText, expiresText, filterChips, fullTime, monitorLabel, stateCopy, stateTag,
} from '@/utils/relayInbound'

const props = defineProps({
  monitor: { type: Object, required: true },
  nowSec: { type: Number, default: () => Date.now() / 1000 },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  showSession: { type: Boolean, default: true },
})
const emit = defineEmits(['toggle-pause', 'delete'])

const PAUSE_TITLE = 'Pause: stop delivering now. Resuming starts from the moment you resume; messages in between are not delivered. Applies immediately.'
const RESUME_TITLE = 'Resume: start again from now. A monitor suspended by the system stays suspended. Applies immediately.'
const DELETE_TITLE = 'Delete: stops it and removes queued, not yet delivered content. The agent can create it again. Applies immediately.'
const KINDS = { monitor_thread: 'Thread', monitor_channel: 'Channel', monitor_mentions: 'Mentions', monitor_dms: 'Direct messages' }

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
.mon-btn:hover:not(:disabled) { color: var(--bs-body-color); }
.mon-btn:disabled { opacity: 0.4; cursor: default; }
.mon-btn--text { font-size: 11px; text-decoration: underline; }
.mon-glyph { flex: 0 0 auto; color: var(--bs-secondary-color); }
.mon-label { flex: 0 1 220px; min-width: 60px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mon-chips { display: inline-flex; gap: 4px; flex: 0 0 auto; }
.mon-session { flex: 1 1 120px; min-width: 40px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--bs-secondary-color); }
.mon-counts, .mon-when { flex: 0 0 auto; color: var(--bs-secondary-color); white-space: nowrap; }
.mon-actions { margin-left: auto; display: inline-flex; align-items: center; gap: 2px; flex: 0 0 auto; }
.mon-confirm { color: var(--bs-secondary-color); }
.mon-error { color: #f87171; font-size: 11px; padding: 2px 0 0 26px; }
.mon-detail { padding: 6px 0 4px 26px; color: var(--bs-body-color); }
.mon-detail dl { display: grid; grid-template-columns: 80px 1fr; gap: 4px 10px; margin: 0; }
.mon-detail dt { color: var(--bs-secondary-color); font-weight: 500; }
.mon-detail dd { margin: 0; word-break: break-word; }
.detail-chip { display: inline-block; margin-right: 4px; padding: 0 5px; border: 1px solid var(--bs-border-color); border-radius: 3px; font-size: 10px; }
.muted { color: var(--bs-secondary-color); }
@media (max-width: 600px) {
  .mon-line { flex-wrap: wrap; row-gap: 2px; }
  .mon-label { flex: 1 1 100px; }
  .mon-session { flex: 1 1 100%; order: 10; padding-left: 26px; }
  .mon-when { display: none; }
  .mon-btn, .mon-expand { min-width: 32px; min-height: 32px; font-size: 14px; }
  .mon-detail { padding-left: 8px; }
  .mon-detail dl { grid-template-columns: 1fr; gap: 0; }
  .mon-detail dt { margin-top: 4px; }
}
</style>
