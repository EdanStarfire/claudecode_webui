<template>
  <div class="audit" data-testid="relay-audit">
    <div class="audit-filters">
      <select v-model="sessionFilter" class="form-select form-select-sm" aria-label="Session" data-testid="audit-session">
        <option value="">All sessions</option>
        <option v-for="s in sessionOptions" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <input
        v-model="channelFilter"
        class="form-control form-control-sm"
        placeholder="Channel name or ID"
        aria-label="Channel"
        data-testid="audit-channel"
      />
      <select v-model="outcomeFilter" class="form-select form-select-sm" aria-label="Outcome" data-testid="audit-outcome">
        <option value="">Any outcome</option>
        <option value="ok">ok</option>
        <option value="denied">denied</option>
        <option value="error">error</option>
      </select>
    </div>

    <div v-if="error" class="audit-error" role="alert" data-testid="audit-error">{{ error }}</div>
    <div v-else-if="!loading && !shown.length" class="audit-empty" data-testid="audit-empty">No entries.</div>

    <table v-if="shown.length" class="audit-table" data-testid="audit-table">
      <thead>
        <tr><th>Time</th><th>Session</th><th>Action</th><th>Channel</th><th>Outcome</th><th>Detail</th></tr>
      </thead>
      <tbody>
        <tr v-for="(r, i) in shown" :key="`${r.ts}-${i}`" class="audit-row" :class="`outcome-${r.outcome || 'ok'}`" data-testid="audit-record">
          <td data-label="Time">{{ formatTime(r.ts) }}</td>
          <td data-label="Session">{{ r.session_name || r.handle || r.session_id || '' }}</td>
          <td data-label="Action" data-testid="audit-action">{{ actionLabel(r) }}</td>
          <td data-label="Channel" :title="r.channel_id || ''">{{ r.channel_name || r.channel_id || '' }}</td>
          <td data-label="Outcome" class="outcome">{{ r.outcome || '' }}<template v-if="r.code"> · {{ r.code }}</template></td>
          <td data-label="Detail" class="detail" data-testid="audit-detail">{{ detail(r) }}</td>
        </tr>
      </tbody>
    </table>

    <button
      v-if="canLoadOlder"
      type="button"
      class="btn btn-sm btn-outline-secondary older"
      :disabled="loading"
      data-testid="audit-older"
      @click="load(false)"
    >{{ loading ? 'Loading…' : 'Load older' }}</button>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRelayStore } from '@/stores/relay'
import { useSessionStore } from '@/stores/session'

const props = defineProps({ relayId: { type: String, required: true } })

const PAGE = 100
const PREVIEW_CAP = 200
const TOOL_PREFIX = 'mcp__relay_slack__'
const ACTIONS = {
  post: 'Post', reply: 'Reply', upload_file: 'Upload', react: 'React', mark_read: 'Mark read',
  get_identity: 'Get identity', find_channels: 'Find channels', read_thread: 'Read thread', search: 'Search',
}

const store = useRelayStore()
const sessionStore = useSessionStore()
const route = useRoute()

const sessionFilter = ref(typeof route.query.session === 'string' ? route.query.session : '')
const channelFilter = ref(typeof route.query.channel === 'string' ? route.query.channel : '')
const outcomeFilter = ref('')
const records = ref([])
const loading = ref(false)
const error = ref('')
const exhausted = ref(false)
let seq = 0
let timer = null

const sessionOptions = computed(() => {
  const list = [...sessionStore.sessions.values()]
    .filter(s => s.session_id)
    .map(s => ({ id: s.session_id, name: s.name || s.session_id }))
    .sort((a, b) => a.name.localeCompare(b.name))
  if (sessionFilter.value && !list.some(s => s.id === sessionFilter.value)) {
    list.push({ id: sessionFilter.value, name: sessionFilter.value })
  }
  return list
})

const shown = computed(() =>
  outcomeFilter.value ? records.value.filter(r => r.outcome === outcomeFilter.value) : records.value)
const canLoadOlder = computed(() => records.value.length > 0 && !exhausted.value)

function actionLabel(r) {
  if (r.event === 'policy_change') return 'Policy change'
  const tool = (r.tool || '').replace(TOOL_PREFIX, '')
  return ACTIONS[tool] || tool || r.event || ''
}

function detail(r) {
  if (r.event === 'policy_change') return r.policy ? `→ ${r.policy}` : ''
  const parts = []
  if (r.tool === 'find_channels' || r.tool === 'get_identity') {
    if (r.query_preview) parts.push(`“${r.query_preview}”`)
    if (r.kinds?.length) parts.push(Array.isArray(r.kinds) ? r.kinds.join(', ') : String(r.kinds))
    if (r.result_count != null) parts.push(`${r.result_count} result${r.result_count === 1 ? '' : 's'}`)
    return parts.join(' · ')
  }
  if (r.text_preview) return r.text_preview.slice(0, PREVIEW_CAP)
  if (r.policy) return `policy ${r.policy}`
  return ''
}

function formatTime(ts) {
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? (ts || '') : d.toLocaleString()
}

async function load(reset) {
  const mine = ++seq
  loading.value = true
  error.value = ''
  try {
    const before = reset ? null : records.value[records.value.length - 1]?.ts
    const page = await store.loadAudit(props.relayId, {
      sessionId: sessionFilter.value, channel: channelFilter.value.trim(), limit: PAGE, before,
    })
    if (mine !== seq) return
    records.value = reset ? page : [...records.value, ...page]
    exhausted.value = page.length < PAGE
  } catch (e) {
    if (mine === seq) error.value = e.message
  } finally {
    if (mine === seq) loading.value = false
  }
}

// A deep link (info modal -> Audit) can arrive while this page is already open.
watch(() => [route.query.session, route.query.channel], ([session, channel]) => {
  sessionFilter.value = typeof session === 'string' ? session : ''
  channelFilter.value = typeof channel === 'string' ? channel : ''
})

watch([sessionFilter, channelFilter], () => {
  clearTimeout(timer)
  timer = setTimeout(() => load(true), 250)
})

onMounted(() => load(true))
onBeforeUnmount(() => clearTimeout(timer))
defineExpose({ reload: () => load(true) })
</script>

<style scoped>
.audit { display: flex; flex-direction: column; gap: 10px; }
.audit-filters { display: flex; gap: 8px; flex-wrap: wrap; }
.audit-filters > * { flex: 1; min-width: 140px; max-width: 260px; }
.audit-error { color: #f87171; font-size: 12px; }
.audit-empty { color: var(--bs-secondary-color); font-size: 13px; }
.audit-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.audit-table th { text-align: left; color: var(--bs-secondary-color); font-weight: 500; padding: 4px 8px; border-bottom: 1px solid var(--bs-border-color); }
.audit-table td { padding: 5px 8px; vertical-align: top; border-bottom: 1px solid var(--bs-border-color); }
.detail { white-space: pre-wrap; word-break: break-word; max-width: 360px; }
.outcome-denied .outcome { color: #d29922; }
.outcome-error .outcome { color: #f87171; }
.older { align-self: flex-start; }
@media (max-width: 600px) {
  .audit-filters > * { max-width: none; }
  .audit-table, .audit-table tbody, .audit-table tr, .audit-table td { display: block; width: 100%; }
  .audit-table thead { display: none; }
  .audit-table tr { border: 1px solid var(--bs-border-color); border-radius: 6px; padding: 6px 8px; margin-bottom: 8px; }
  .audit-table td { border: 0; padding: 2px 0; display: flex; gap: 8px; }
  .audit-table td::before { content: attr(data-label); flex: 0 0 64px; color: var(--bs-secondary-color); }
  .detail { max-width: none; }
}
</style>
