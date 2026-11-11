<template>
  <div class="feed" data-testid="activity-feed">
    <div class="feed-head">
      <span class="feed-chips" role="group" aria-label="Activity filter">
        <button
          v-for="c in ACTIVITY_CHIPS"
          :key="c.key"
          type="button"
          class="filter-chip"
          :class="{ on: chip === c.key }"
          :aria-pressed="chip === c.key"
          :data-testid="`activity-chip-${c.key}`"
          @click="chip = c.key"
        >{{ c.label }}</button>
      </span>
      <span class="feed-links">
        <button type="button" class="link-btn" :disabled="loading" title="Reload" aria-label="Refresh" data-testid="activity-refresh" @click="load(true)">↻<span class="txt"> Refresh</span></button>
        <router-link class="link-btn" :to="auditLink" title="Open these rows in the Audit tab" data-testid="activity-open-audit">Open in Audit<span aria-hidden="true"> →</span></router-link>
      </span>
    </div>

    <div v-if="error" class="feed-error" role="alert" data-testid="activity-error">{{ error }}</div>
    <div v-else-if="loaded && !shown.length" class="muted" data-testid="activity-empty">No activity yet.</div>

    <div
      v-for="(r, i) in shown"
      :key="`${r.ts}-${i}`"
      class="feed-row"
      :class="{ problem: isProblem(r) }"
      :title="rowTitle(r)"
      data-testid="activity-row"
    >
      <span class="feed-time" :title="fullTime(r.ts)">{{ clockTime(r.ts) }}</span>
      <span v-if="who(r)" class="feed-who" data-testid="activity-session">{{ who(r) }}</span>
      <span class="feed-text" data-testid="activity-text">{{ text(r) }}</span>
      <RelayTag v-if="outcomeTag(r)" :tone="isProblem(r) ? 'amber' : 'muted'" data-testid="activity-outcome">{{ outcomeTag(r) }}</RelayTag>
    </div>

    <button v-if="canOlder" type="button" class="btn btn-sm btn-outline-secondary older" :disabled="loading" data-testid="activity-older" @click="load(false)">
      {{ loading ? 'Loading…' : 'Load older' }}
    </button>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRelayStore } from '@/stores/relay'
import RelayTag from './RelayTag.vue'
import { EVENTS, inboundDetail } from '@/utils/relayAuditLabels'
import { ACTIVITY_CHIPS, clockTime, fullTime, isProblem } from '@/utils/relayInbound'

const props = defineProps({
  relayId: { type: String, required: true },
  active: { type: Boolean, default: true }, // loads when the tab becomes visible; never polls
})

const PAGE = 50
const store = useRelayStore()
const chip = ref('all')
const rows = ref([])
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const exhausted = ref(false)
let seq = 0

const current = computed(() => ACTIVITY_CHIPS.find(c => c.key === chip.value) || ACTIVITY_CHIPS[0])
const shown = computed(() => (current.value.problems ? rows.value.filter(isProblem) : rows.value))
const canOlder = computed(() => rows.value.length > 0 && !exhausted.value)
const auditLink = computed(() => ({ path: '/settings/relays', query: { tab: 'audit', events: current.value.events } }))

const who = (r) => r.session_name || r.handle || ''
function text(r) {
  const label = EVENTS[r.event] || r.event
  const detail = inboundDetail(r) || r.code || ''
  return detail ? `${label} · ${detail}` : label
}
function outcomeTag(r) {
  if (r.event === 'inbound_connection') return ''
  const code = r.event === 'inbound_instruction' && r.code !== r.outcome ? r.code : ''
  return [r.outcome, code].filter(Boolean).join(' · ')
}
const rowTitle = (r) => [r.event, r.outcome, r.code, r.session_id, r.channel_id, r.monitor_id].filter(Boolean).join(' · ')

async function load(reset) {
  const mine = ++seq
  loading.value = true
  error.value = ''
  try {
    const before = reset ? null : rows.value[rows.value.length - 1]?.ts
    const page = await store.loadInboundActivity(props.relayId, { limit: PAGE, before, events: current.value.events })
    if (mine !== seq) return
    rows.value = reset ? page : [...rows.value, ...page]
    exhausted.value = page.length < PAGE
  } catch (e) {
    if (mine === seq) error.value = store.errorMessage(e)
  } finally {
    if (mine === seq) { loading.value = false; loaded.value = true }
  }
}

watch(chip, () => load(true))
watch(() => props.active, (on) => { if (on) load(true) }, { immediate: true })
onBeforeUnmount(() => { seq++ })
defineExpose({ reload: () => load(true) })
</script>

<style scoped>
.feed { display: flex; flex-direction: column; }
.feed-head { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.feed-chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.feed-links { display: inline-flex; gap: 10px; }
.filter-chip {
  background: none; border: 1px solid var(--bs-border-color); border-radius: 10px; padding: 0 9px; font-size: 11px;
  line-height: 20px; color: var(--bs-secondary-color); cursor: pointer;
}
.filter-chip.on { color: var(--bs-body-color); border-color: var(--bs-secondary-color); }
.link-btn { background: none; border: 0; padding: 0; font-size: 11px; color: var(--bs-secondary-color); text-decoration: none; cursor: pointer; }
.link-btn:hover { color: var(--bs-body-color); }
.feed-row { display: flex; align-items: baseline; gap: 8px; font-size: 12px; padding: 3px 0; border-bottom: 1px solid var(--bs-border-color); min-width: 0; }
.feed-time { flex: 0 0 auto; color: var(--bs-secondary-color); font-variant-numeric: tabular-nums; }
.feed-who { flex: 0 1 140px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--bs-secondary-color); }
.feed-text { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.feed-error { color: var(--tool-error-text); font-size: 12px; }
.muted { color: var(--bs-secondary-color); font-size: 12px; }
.older { align-self: flex-start; margin-top: 8px; }
@media (max-width: 600px) {
  .txt { display: none; }
  .feed-who { display: none; }
}
</style>
