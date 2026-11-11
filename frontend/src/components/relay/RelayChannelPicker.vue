<template>
  <div ref="root" class="relay-picker" data-testid="relay-picker">
    <div class="picker-input-row">
      <input
        v-model="query"
        class="form-control form-control-sm"
        type="text"
        role="combobox"
        autocomplete="off"
        :aria-expanded="String(open)"
        :placeholder="placeholder"
        data-testid="relay-picker-input"
        @focus="openPanel"
        @input="onInput"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="chooseActive"
        @keydown.esc="open = false"
      />
    </div>

    <div v-if="open" class="picker-panel" role="listbox" data-testid="relay-picker-panel">
      <div v-if="error" class="picker-error" role="alert" data-testid="relay-picker-error">{{ error }}</div>
      <div v-else-if="needMore" class="picker-empty" data-testid="relay-picker-hint">Type at least 2 characters after @.</div>
      <div v-else-if="loading && !rows.length" class="picker-empty">Searching…</div>
      <div v-else-if="!rows.length && !pastedTarget && !loading" class="picker-empty" data-testid="relay-picker-empty">No matches</div>

      <button
        v-if="pastedTarget"
        type="button"
        class="picker-row"
        :class="{ active: activeIndex === -1 }"
        data-testid="relay-picker-paste"
        @click="emitTarget(pastedTarget, pastedTarget)"
      >
        <SourceMarker :kind="markerOf(pastedTarget)" :template-name="templateName" :profile-name="profileName" />
        <span class="row-label">Use ID <code class="row-id">{{ pastedTarget }}</code></span>
        <span class="row-status">{{ statusText(pastedTarget) }}</span>
      </button>

      <template v-for="group in groups" :key="group.kind">
        <div class="picker-group">{{ group.title }}</div>
        <button
          v-for="row in group.rows"
          :key="row.id"
          type="button"
          class="picker-row"
          role="option"
          :class="{ active: rows.indexOf(row) === activeIndex, disabled: !targetOf(row) }"
          :disabled="!targetOf(row)"
          :data-testid="`relay-picker-row-${row.id}`"
          @click="choose(row)"
        >
          <SourceMarker v-if="targetOf(row)" :kind="markerOf(targetOf(row))" :template-name="templateName" :profile-name="profileName" />
          <span v-else class="marker-slot" />
          <span class="row-label" :title="`${row.label} · ${targetOf(row) || row.id}`">{{ row.label }}</span>
          <span v-if="!targetOf(row)" class="row-status">no DM yet</span>
          <span v-else class="row-status" :class="{ 'is-set': statusFor(targetOf(row)) }">{{ statusText(targetOf(row)) }}</span>
        </button>
      </template>

      <button
        v-if="nextCursor"
        type="button"
        class="picker-more"
        :disabled="loading"
        data-testid="relay-picker-more"
        @click="loadMore"
      >Load more</button>
      <div v-if="truncated" class="picker-empty" data-testid="relay-picker-truncated">Refine the search to see more.</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRelayStore } from '@/stores/relay'
import SourceMarker from '../settings/SourceMarker.vue'

const props = defineProps({
  relayId: { type: String, required: true },
  policyModel: { type: Object, default: null },
  // target -> policy value (or null when unlisted)
  // target -> {policy, kind: 'S'|'T'|'P'} or null (unlisted)
  statusFor: { type: Function, default: () => null },
  templateName: { type: String, default: null },
  profileName: { type: String, default: null },
})
const emit = defineEmits(['select'])

const store = useRelayStore()
const root = ref(null)
const query = ref('')
const open = ref(false)
const loading = ref(false)
const error = ref('')
const rows = ref([])
const nextCursor = ref(null)
const truncated = ref(false)
const activeIndex = ref(0)

const DEBOUNCE_MS = 250
const MIN_PERSON_QUERY = 2
const CHANNEL_KINDS = ['public', 'private']
const KIND_TITLES = { public: 'Channels', private: 'Private channels', dm: 'Direct messages', group_dm: 'Group DMs', person: 'People' }
const KIND_ORDER = Object.keys(KIND_TITLES)

let timer = null
let controller = null
let seq = 0
// The query a cursor belongs to: "Load more" must continue THAT search, not the box's current text.
let searched = { q: '', kinds: null }

const placeholder = '#channel, @person or paste an ID'

// "@" searches people, "#" searches channels, anything else channels + DMs + group DMs.
const parsed = computed(() => {
  const raw = query.value.trim()
  if (raw.startsWith('@')) return { q: raw.slice(1).trim(), kinds: ['person'] }
  if (raw.startsWith('#')) return { q: raw.slice(1).trim(), kinds: CHANNEL_KINDS }
  return { q: raw, kinds: null }
})
const needMore = computed(() => parsed.value.kinds?.[0] === 'person' && parsed.value.q.length < MIN_PERSON_QUERY)

const pastedTarget = computed(() => {
  const pattern = props.policyModel?.target_pattern
  const text = query.value.trim().replace(/^[#@]/, '')
  if (!pattern || !text) return null
  try {
    return new RegExp(pattern).test(text) ? text : null
  } catch {
    return null
  }
})

const groups = computed(() => KIND_ORDER
  .map(kind => ({ kind, title: KIND_TITLES[kind], rows: rows.value.filter(r => r.kind === kind) }))
  .filter(g => g.rows.length))

/** The policy target a row stands for: a person maps to their DM (null when none is known). */
function targetOf(row) {
  return row.kind === 'person' ? (row.dm_id || null) : row.id
}

function statusText(target) {
  return props.statusFor(target)?.policy || 'unlisted'
}

// the app's SourceMarker kind for a target: where its value comes from, EMPTY when unset
const markerOf = (target) => props.statusFor(target)?.kind || 'EMPTY'

async function search(reset) {
  clearTimeout(timer)
  if (controller) controller.abort()
  controller = new AbortController()
  const mine = ++seq
  const cursor = reset ? null : nextCursor.value
  if (reset) searched = { q: parsed.value.q, kinds: parsed.value.kinds }
  if (reset && needMore.value) {
    rows.value = []
    nextCursor.value = null
    error.value = ''
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const page = await store.searchConversations(props.relayId, {
      q: searched.q, kinds: searched.kinds, cursor, signal: controller.signal,
    })
    if (mine !== seq) return
    rows.value = reset ? page.entries : [...rows.value, ...page.entries]
    nextCursor.value = page.next_cursor || null
    truncated.value = !!page.truncated
    if (reset) activeIndex.value = rows.value.length ? 0 : -1
    for (const r of page.entries) {
      const t = targetOf(r)
      if (t) store.rememberLabel(props.relayId, t, r.label)
    }
  } catch (e) {
    if (mine !== seq || e?.name === 'AbortError') return
    error.value = e.message
    if (reset) rows.value = []
  } finally {
    if (mine === seq) loading.value = false
  }
}

function loadMore() {
  return search(false)
}

function onInput() {
  open.value = true
  if (needMore.value) { // nothing to search yet: drop the previous results at once
    rows.value = []
    nextCursor.value = null
  }
  clearTimeout(timer)
  timer = setTimeout(() => search(true), DEBOUNCE_MS)
}

function openPanel() {
  const wasOpen = open.value
  open.value = true
  document.addEventListener('mousedown', onOutside)
  if (!wasOpen && !rows.value.length) search(true)
}

function onOutside(event) {
  if (root.value && !root.value.contains(event.target)) {
    open.value = false
    document.removeEventListener('mousedown', onOutside)
  }
}

function move(delta) {
  const selectable = rows.value.map((r, i) => (targetOf(r) ? i : -1)).filter(i => i >= 0)
  if (!selectable.length) return
  const at = selectable.indexOf(activeIndex.value)
  const next = at < 0 ? 0 : (at + delta + selectable.length) % selectable.length
  activeIndex.value = selectable[next]
}

function emitTarget(target, label) {
  emit('select', { target, label })
  open.value = false
  query.value = ''
}

function choose(row) {
  const target = targetOf(row)
  if (target) emitTarget(target, row.label)
}

function chooseActive() {
  if (pastedTarget.value && activeIndex.value < 0) return emitTarget(pastedTarget.value, pastedTarget.value)
  const row = rows.value[activeIndex.value]
  if (row) choose(row)
  else if (pastedTarget.value) emitTarget(pastedTarget.value, pastedTarget.value)
}

onBeforeUnmount(() => {
  clearTimeout(timer)
  if (controller) controller.abort()
  document.removeEventListener('mousedown', onOutside)
})
</script>

<style scoped>
.relay-picker { flex: 1; min-width: 0; } /* the panel anchors to the editor's add row */
.picker-input-row { display: flex; }
.picker-input-row > input { flex: 1; min-width: 0; }
.picker-panel {
  position: absolute; z-index: 20; left: 0; right: 0; margin-top: 4px; max-height: 320px; overflow-y: auto;
  background: var(--bs-body-bg); border: 1px solid var(--bs-border-color); border-radius: 6px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.25);
}
.picker-group { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--bs-secondary-color); padding: 6px 10px 2px; }
.picker-row {
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 5px 10px; background: none; border: 0;
  text-align: left; color: var(--bs-body-color); font-size: 13px; cursor: pointer;
}
.picker-row:hover:not(:disabled), .picker-row.active { background: var(--bs-secondary-bg); }
.picker-row.disabled { opacity: 0.55; cursor: default; }
.row-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-id { font-size: 11px; color: var(--bs-secondary-color); }
.marker-slot { flex: none; width: 16px; }
.row-status { font-size: 11px; color: var(--bs-secondary-color); min-width: 56px; text-align: right; }
.row-status.is-set { color: var(--bs-body-color); font-weight: 600; }
.picker-more { width: 100%; padding: 6px; background: none; border: 0; border-top: 1px solid var(--bs-border-color); font-size: 12px; color: var(--bs-link-color); }
.picker-empty, .picker-error { padding: 8px 10px; font-size: 12px; color: var(--bs-secondary-color); }
.picker-error { color: var(--tool-error-text); }
@media (max-width: 600px) {
  .row-id { display: none; }
  .picker-row { padding: 4px 6px; font-size: 12px; }
}
</style>
