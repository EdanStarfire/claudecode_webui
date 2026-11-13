<template>
  <div class="mon-list" data-testid="monitor-list">
    <div class="mon-filters">
      <span class="mon-chips" role="group" aria-label="State">
        <button
          v-for="c in STATE_CHIPS"
          :key="c.key"
          type="button"
          class="filter-chip"
          :class="{ on: status === c.key }"
          :aria-pressed="status === c.key"
          :data-testid="`monitor-filter-${c.key || 'all'}`"
          @click="status = c.key"
        >{{ c.label }}</button>
      </span>
      <select v-if="!sessionId" v-model="sessionFilter" class="form-select form-select-sm" aria-label="Session" data-testid="monitor-filter-session">
        <option value="">All sessions</option>
        <option v-for="s in sessionOptions" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <select v-model="mode" class="form-select form-select-sm" aria-label="Type" data-testid="monitor-filter-mode">
        <option v-for="o in MODE_OPTIONS" :key="o.key" :value="o.key">{{ o.label }}</option>
      </select>
      <select v-model="kind" class="form-select form-select-sm" aria-label="Kind" data-testid="monitor-filter-kind">
        <option value="">All kinds</option>
        <option v-for="(label, k) in KIND_OPTIONS" :key="k" :value="k">{{ label }}</option>
      </select>
      <select v-model="sortBy" class="form-select form-select-sm" aria-label="Sort" data-testid="monitor-sort">
        <option value="created">Sort: created</option>
        <option value="next">Sort: next run</option>
      </select>
    </div>

    <div v-if="loadError" class="mon-load-error" role="alert" data-testid="monitor-load-error">{{ loadError }}</div>
    <div v-else-if="notRunning" class="muted" data-testid="monitor-not-running">{{ sessionId ? 'Monitors are unavailable for this relay right now.' : 'Inbound is not running, so there is nothing to list.' }}</div>
    <div v-else-if="loaded && !rows.length" class="muted" data-testid="monitor-empty">
      {{ hasFilter ? 'No monitors match.' : (sessionId ? 'No monitors. The agent creates them with its monitor tools.' : 'No monitors yet. Agents create them with the monitor tools.') }}
    </div>

    <div v-if="removedNote" class="muted" data-testid="monitor-removed-note">A monitor was {{ removedNote }}.</div>

    <RelayMonitorRow
      v-for="m in sortedRows"
      :key="m.id"
      :monitor="m"
      :now-sec="nowSec"
      :busy="busy.has(m.id)"
      :running="running.has(m.id)"
      :note="notes[m.id] || ''"
      :peers="rows"
      :error="errors[m.id] || ''"
      :show-session="!sessionId"
      @toggle-pause="togglePause"
      @run="runNow"
      @delete="remove"
    />

    <button
      v-if="rows.length < total"
      type="button"
      class="btn btn-sm btn-outline-secondary more"
      :disabled="loadingMore"
      data-testid="monitor-more"
      @click="loadMore"
    >{{ loadingMore ? 'Loading…' : `Load more (${total - rows.length})` }}</button>

    <div class="muted foot" data-testid="monitor-foot">
      <template v-if="hasScheduled"><span data-testid="monitor-repeat-foot">{{ REPEAT_NOTICE }}</span>{{ ' ' }}</template>Applies immediately. Nothing here needs saving.
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRelayStore } from '@/stores/relay'
import { useSessionStore } from '@/stores/session'
import RelayMonitorRow from './RelayMonitorRow.vue'
import { REPEAT_NOTICE, runNowError, toEpoch } from '@/utils/relayInbound'

const props = defineProps({
  relayId: { type: String, required: true },
  sessionId: { type: String, default: '' }, // locks the list to one session (session-qualified routes; the session's Monitors block)
  initialSession: { type: String, default: '' },
  active: { type: Boolean, default: true }, // polls only while visible
  pollMs: { type: Number, default: 10000 },
})
const emit = defineEmits(['changed'])

const PAGE = 100
const MAX_PAGE = 200
const STATE_CHIPS = [
  { key: '', label: 'All' }, { key: 'active', label: 'Active' },
  { key: 'paused', label: 'Paused' }, { key: 'suspended', label: 'Suspended' },
]
const KIND_OPTIONS = {
  monitor_thread: 'Thread', monitor_channel: 'Channel', monitor_mentions: 'Mentions', monitor_dms: 'Direct messages',
  monitor_unread: 'Unread',
}
const MODE_OPTIONS = [
  { key: '', label: 'All types' }, { key: 'live', label: 'Live' }, { key: 'digest', label: 'Digest' }, { key: 'unread', label: 'Unread' },
]
const RUNNING_MAX_MS = 8000 // "Running…" ends at the latest then, whatever the next refresh shows

const store = useRelayStore()
const sessionStore = useSessionStore()

const status = ref('')
const kind = ref('')
const mode = ref('')
const sortBy = ref('created')
const sessionFilter = ref(props.initialSession)
const rows = ref([])
const total = ref(0)
const loaded = ref(false)
const loadError = ref('')
const notRunning = ref(false)
const loadingMore = ref(false)
const busy = reactive(new Set())
const errors = reactive({})
const notes = reactive({})
const running = reactive(new Set())
const runBaseline = {} // monitor id -> last_run.at when Run now was clicked
const runTimers = {}
const nowSec = ref(Date.now() / 1000)
let seq = 0
let moreSeq = 0
let filterGen = 0
let unmounted = false
let needReset = false // a reset load was superseded by an action: the reload keeps it
let reloadAfter = null // a load skipped while an action was in flight: re-run when it ends
let timer = null

const effectiveSession = computed(() => props.sessionId || sessionFilter.value)
const hasFilter = computed(() => !!(status.value || kind.value || mode.value || (!props.sessionId && sessionFilter.value)))
const hasScheduled = computed(() => rows.value.some(m => m.mode === 'digest' || m.mode === 'unread'))
const sortedRows = computed(() => {
  if (sortBy.value !== 'next') return rows.value
  const next = (m) => (m.mode === 'live' || !m.mode ? Infinity : (toEpoch(m.next_run) ?? Infinity))
  return [...rows.value].sort((a, b) => next(a) - next(b))
})
const sessionOptions = computed(() => {
  const list = [...sessionStore.sessions.values()]
    .filter(s => s.session_id)
    .map(s => ({ id: s.session_id, name: s.name || s.session_id }))
  for (const m of rows.value) { // sessions that no longer exist still show their stored name
    if (m.session_id && !list.some(s => s.id === m.session_id)) list.push({ id: m.session_id, name: m.session_name || m.session_id })
  }
  if (sessionFilter.value && !list.some(s => s.id === sessionFilter.value)) list.push({ id: sessionFilter.value, name: sessionFilter.value })
  return list.sort((a, b) => a.name.localeCompare(b.name))
})

function query(extra = {}) {
  // a locked session list uses the session-qualified routes (the session's Monitors block, 3d-ii)
  return { sessionId: effectiveSession.value, qualified: !!props.sessionId, status: status.value, kind: kind.value, mode: mode.value, ...extra }
}

/** Reload the loaded span (a poll, a filter change, after an action). Skipped while an action is in flight. */
async function load({ reset = false } = {}) {
  if (unmounted) return
  if (reset) needReset = true
  const mine = ++seq
  if (reset) removedNote.value = ''
  try {
    const limit = reset ? PAGE : Math.min(MAX_PAGE, Math.max(PAGE, rows.value.length))
    const res = await store.loadMonitors(props.relayId, query({ limit }))
    if (mine !== seq) return
    if (reset) needReset = false
    if (busy.size) { // the optimistic rows win; reload once the action has finished
      reloadAfter = { reset }
      return
    }
    rows.value = res.monitors || []
    total.value = res.total ?? rows.value.length
    notRunning.value = !!res.notRunning
    loadError.value = ''
    nowSec.value = Date.now() / 1000
    for (const id of [...running]) { // a new last run ends the optimistic "Running…"
      const row = rows.value.find(r => r.id === id)
      if (!row || (row.last_run?.at ?? null) !== runBaseline[id]) finishRun(id)
    }
  } catch (e) {
    if (mine === seq) loadError.value = e.message
  } finally {
    if (mine === seq) loaded.value = true
  }
}

async function loadMore() {
  loadingMore.value = true
  const mine = ++moreSeq
  const gen = filterGen
  try {
    const res = await store.loadMonitors(props.relayId, query({ limit: PAGE, offset: rows.value.length }))
    if (mine !== moreSeq || gen !== filterGen || busy.size) return  // stale: a filter or an action intervened
    const have = new Set(rows.value.map(r => r.id))
    rows.value = [...rows.value, ...(res.monitors || []).filter(r => !have.has(r.id))]
    total.value = res.total ?? total.value
  } catch (e) {
    if (mine === moreSeq) loadError.value = e.message
  } finally {
    loadingMore.value = false
  }
}

function replace(id, next) {
  const i = rows.value.findIndex(r => r.id === id)
  if (i >= 0) rows.value.splice(i, 1, next)
}

async function togglePause(monitor) {
  const id = monitor.id
  const before = { ...monitor }
  const paused = !before.paused
  delete errors[id]
  busy.add(id)
  seq++ // an older in-flight poll must not overwrite this change
  // optimistic: the owner's pause flag wins over any suspension in the effective state
  replace(id, { ...before, paused, effective_state: paused ? 'paused' : (before.status === 'active' ? 'active' : 'suspended') })
  try {
    const next = await store.setMonitorPaused(props.relayId, id, paused, props.sessionId || null)
    if (next && next.id) replace(id, { ...before, ...next }) // the server decides (resume never lifts a suspension)
    emit('changed')
  } catch (e) {
    if (e.status === 404) dropRow(id, 'already removed')
    else {
      replace(id, before)
      errors[id] = e.message
    }
  } finally {
    busy.delete(id)
    settleAction()
  }
}

function finishRun(id) {
  clearTimeout(runTimers[id])
  delete runTimers[id]
  delete runBaseline[id]
  running.delete(id)
  delete notes[id]
}

async function runNow(monitor) {
  const id = monitor.id
  if (running.has(id)) return // no double submit
  delete errors[id]
  running.add(id)
  runBaseline[id] = monitor.last_run?.at ?? null
  notes[id] = 'Started: the result appears in "last run".'
  runTimers[id] = setTimeout(() => { finishRun(id); load() }, RUNNING_MAX_MS)
  try {
    const res = await store.runMonitor(props.relayId, id, props.sessionId || null)
    if (res?.monitor?.id) replace(id, { ...monitor, ...res.monitor })
    emit('changed')
    setTimeout(() => { if (!unmounted) load() }, 2500) // pick the result up without waiting for the poll
  } catch (e) {
    finishRun(id)
    if (e.status === 404) dropRow(id, 'already removed')
    else if (e.status === 422) delete errors[id] // not a scheduled monitor: nothing to show
    else errors[id] = runNowError({ status: e.status, code: e.code, retryAfter: e.retryAfter })
  }
}

function settleAction() {
  if (unmounted) return
  seq++ // a poll that started during the action carries pre-action state
  if (!busy.size) {
    const again = { reset: needReset || !!reloadAfter?.reset }
    reloadAfter = null
    load(again)
  }
}

const removedNote = ref('')
function dropRow(id, note) {
  rows.value = rows.value.filter(r => r.id !== id)
  total.value = Math.max(0, total.value - 1)
  delete errors[id]
  removedNote.value = note
}

async function remove(monitor) {
  const id = monitor.id
  const index = rows.value.findIndex(r => r.id === id)
  delete errors[id]
  busy.add(id)
  seq++
  rows.value = rows.value.filter(r => r.id !== id)
  total.value = Math.max(0, total.value - 1)
  try {
    await store.deleteMonitor(props.relayId, id, props.sessionId || null)
    emit('changed')
  } catch (e) {
    if (e.status === 404) {
      emit('changed') // it was already gone: the row stays removed
    } else {
      rows.value.splice(Math.min(index, rows.value.length), 0, monitor) // rollback at the old position
      total.value += 1
      errors[id] = e.message
    }
  } finally {
    busy.delete(id)
    settleAction()
  }
}

function stopPolling() {
  clearInterval(timer)
  timer = null
}
function startPolling() {
  stopPolling()
  timer = setInterval(() => { if (!document.hidden) load() }, props.pollMs)
}

// a deep link (?session=) can arrive while the page is already open
watch(() => props.initialSession, (id) => { sessionFilter.value = id })
watch([status, kind, mode, sessionFilter], () => { filterGen++; load({ reset: true }) })
watch(() => props.active, (on) => {
  if (on) { load(); startPolling() } else stopPolling()
}, { immediate: true })
function onVisible() { if (!document.hidden && props.active) load() }
document.addEventListener('visibilitychange', onVisible)
onBeforeUnmount(() => {
  unmounted = true
  Object.values(runTimers).forEach(clearTimeout)
  stopPolling()
  seq++
  document.removeEventListener('visibilitychange', onVisible)
})
defineExpose({ reload: () => load(), rows })
</script>

<style scoped>
.mon-list { display: flex; flex-direction: column; }
.mon-filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 6px; }
.mon-filters .form-select { width: auto; max-width: 200px; flex: 0 1 auto; }
.mon-chips { display: inline-flex; gap: 4px; }
.filter-chip {
  background: none; border: 1px solid var(--bs-border-color); border-radius: 10px; padding: 0 9px; font-size: 11px;
  line-height: 20px; color: var(--bs-secondary-color); cursor: pointer;
}
.filter-chip.on { color: var(--bs-body-color); border-color: var(--bs-secondary-color); }
.mon-load-error { color: var(--tool-error-text); font-size: 12px; }
.muted { color: var(--bs-secondary-color); font-size: 12px; }
.foot { margin-top: 8px; }
.more { align-self: flex-start; margin-top: 6px; }
@media (max-width: 600px) {
  .mon-filters .form-select { max-width: none; flex: 1 1 120px; }
}
</style>
