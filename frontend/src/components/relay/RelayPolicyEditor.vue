<template>
  <div class="policy-editor" data-testid="relay-policy-editor">
    <div class="editor-head">
      <span class="editor-title">Channel policies</span>
      <span v-if="mode === 'live'" class="editor-note" data-testid="relay-policy-live-note">Applies immediately.</span>
    </div>

    <div v-if="loadError" class="editor-error" role="alert" data-testid="relay-policy-load-error">{{ loadError }}</div>
    <div v-else-if="!model" class="editor-muted">Loading…</div>

    <template v-else>
      <div v-if="!rows.length" class="editor-muted" data-testid="relay-policy-empty">No channel policies yet.</div>
      <ul v-else class="policy-rows">
        <li
          v-for="row in rows"
          :key="row.target"
          class="policy-row"
          :title="`${labelOf(row.target)} · ${row.target}`"
          :data-testid="`relay-policy-row-${row.target}`"
        >
          <SourceMarker :kind="markerKind(row)" :template-name="templateName" :profile-name="profileName" data-testid="relay-policy-tier" />
          <span class="row-label" data-testid="relay-policy-label">{{ labelOf(row.target) }}</span>
          <RelayPolicyStrip
            :model="model"
            :model-value="row.policy"
            :disabled="busy.has(row.target)"
            :label="`Policy for ${labelOf(row.target)}`"
            @update:model-value="change(row.target, $event)"
          />
          <button
            v-if="row.own"
            type="button"
            class="field-reset-btn"
            title="Reset to inherited value"
            :aria-label="`Reset policy for ${labelOf(row.target)}`"
            :disabled="busy.has(row.target)"
            data-testid="relay-policy-remove"
            @click="remove(row.target)"
          >↩</button>
          <span v-else class="reset-slot" />
        </li>
      </ul>

      <div v-if="error" class="editor-error" role="alert" data-testid="relay-policy-error">{{ error }}</div>

      <div class="add-row">
        <RelayChannelPicker
          :relay-id="effectiveRelayId"
          :policy-model="model"
          :status-for="statusFor"
          :template-name="templateName"
          :profile-name="profileName"
          @select="add"
        />
        <RelayPolicyStrip v-model="newPolicy" :model="model" label="Policy for the new entry" data-testid="relay-policy-new-value" />
        <span class="reset-slot" />
      </div>

      <details class="explain" data-testid="relay-policy-explain">
        <summary>What do these mean?</summary>
        <ul>
          <li v-for="v in model.values" :key="v"><strong>{{ v }}</strong><template v-if="model.value_help?.[v]"> — {{ model.value_help[v] }}</template></li>
        </ul>
        <p>Unlisted channels ask on first use.</p>
      </details>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRelayStore } from '@/stores/relay'
import RelayChannelPicker from './RelayChannelPicker.vue'
import RelayPolicyStrip from './RelayPolicyStrip.vue'
import SourceMarker from '../settings/SourceMarker.vue'

/**
 * Channel policy editor (issue #2103, UI stage 2b), rendered from the adapter's policy model.
 *
 * live  - a session's policies: every change is its own request to the relay-qualified policy
 *         endpoints and the state is replaced by the returned session-relay payload.
 * local - a profile/template/schedule form: edits `modelValue` (the OWN-tier nested dict
 *         {relay_id: {target: policy}}) and emits `update:modelValue` with own-tier entries only.
 *         `inherited` ({target: {policy, source}}) is display-only and is never emitted.
 */
const props = defineProps({
  mode: { type: String, default: 'live' },
  sessionId: { type: String, default: '' },
  relayId: { type: String, default: '' },
  policyModel: { type: Object, default: null },
  modelValue: { type: Object, default: null },
  inherited: { type: Object, default: () => ({}) },
  templateName: { type: String, default: null },
  profileName: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue'])

const store = useRelayStore()
const payload = ref(null)
const loadError = ref('')
const error = ref('')
const busy = reactive(new Set())
const labels = ref({})
const newPolicy = ref('')

const isLive = computed(() => props.mode === 'live')
const model = computed(() => (isLive.value ? payload.value?.policy_model : props.policyModel) || null)
const effectiveRelayId = computed(() => (isLive.value ? payload.value?.relay_id : props.relayId) || '')

const ownEntries = computed(() => (props.modelValue && props.modelValue[props.relayId]) || {})

// target -> {policy, source, own}
const rows = computed(() => {
  const merged = {}
  if (isLive.value) {
    for (const [t, p] of Object.entries(payload.value?.policies || {})) {
      merged[t] = { target: t, policy: p.policy, source: p.source, own: p.source === 'session' }
    }
  } else {
    for (const [t, p] of Object.entries(props.inherited || {})) {
      merged[t] = { target: t, policy: p.policy, source: p.source, own: false }
    }
    for (const [t, policy] of Object.entries(ownEntries.value)) {
      merged[t] = { target: t, policy, source: 'own', own: true }
    }
  }
  return Object.values(merged).sort((a, b) =>
    labelOf(a.target).localeCompare(labelOf(b.target)) || a.target.localeCompare(b.target))
})

const labelOf = (target) => labels.value[target] || target
const statusFor = (target) => {
  const row = rows.value.find(r => r.target === target)
  return row ? { policy: row.policy, kind: markerKind(row) } : null
}
// the app's SourceMarker kinds: own tier = S, inherited = T / P
const markerKind = (row) => (row.own || row.source === 'session' ? 'S' : row.source === 'template' ? 'T' : 'P')

let loadSeq = 0

async function loadLive() {
  const mine = ++loadSeq
  loadError.value = ''
  payload.value = null
  if (!props.sessionId) return
  try {
    const result = await store.getSessionRelay(props.sessionId)
    if (mine === loadSeq) payload.value = result
  } catch (e) {
    if (mine === loadSeq) loadError.value = e.message
  }
}

watch(() => [props.mode, props.sessionId], () => { if (isLive.value) loadLive() }, { immediate: true })

watch(model, (m) => {
  if (m && !m.values.includes(newPolicy.value)) newPolicy.value = m.values[0] || ''
}, { immediate: true })

watch(
  () => `${effectiveRelayId.value}|${rows.value.map(r => r.target).sort().join(',')}`,
  async () => {
    const rid = effectiveRelayId.value
    const targets = rows.value.map(r => r.target)
    if (!rid || !targets.length) return
    const resolved = await store.resolveLabels(rid, targets)
    labels.value = { ...labels.value, ...resolved }
  },
  { immediate: true },
)

function validTarget(target) {
  const pattern = model.value?.target_pattern
  if (!pattern) return true
  try {
    return new RegExp(pattern).test(target)
  } catch {
    return true
  }
}

function emitOwn(entries) {
  const next = { ...(props.modelValue || {}) }
  if (Object.keys(entries).length) next[props.relayId] = entries
  else delete next[props.relayId]
  emit('update:modelValue', next)
}

// Live writes run one at a time: a response always reflects every earlier edit, and a failed
// edit rolls back only its own target.
let writeQueue = Promise.resolve()

function liveWrite(target, optimistic, request) {
  const run = async () => {
    error.value = ''
    busy.add(target)
    const previous = payload.value?.policies?.[target]
    const policies = { ...(payload.value?.policies || {}) }
    if (optimistic) policies[target] = optimistic
    else delete policies[target]
    payload.value = { ...payload.value, policies }
    try {
      payload.value = await request()
    } catch (e) {
      const rolled = { ...(payload.value?.policies || {}) }
      if (previous) rolled[target] = previous
      else delete rolled[target]
      payload.value = { ...payload.value, policies: rolled }
      error.value = e.message
    } finally {
      busy.delete(target)
    }
  }
  const next = writeQueue.then(run)
  writeQueue = next.catch(() => {})
  return next
}

async function write(target, policy) {
  if (!validTarget(target)) {
    error.value = `Not a valid ${model.value?.target_label || 'target'}.`
    return
  }
  if (isLive.value) {
    await liveWrite(target, { policy, source: 'session' },
      () => store.setPolicy(props.sessionId, effectiveRelayId.value, target, policy))
  } else {
    error.value = ''
    emitOwn({ ...ownEntries.value, [target]: policy })
  }
}

const change = (target, policy) => write(target, policy)

async function remove(target) {
  if (isLive.value) {
    // removing the session entry reveals a lower tier's value (if any) after the refresh
    await liveWrite(target, null,
      () => store.removePolicy(props.sessionId, effectiveRelayId.value, target))
  } else {
    const next = { ...ownEntries.value }
    delete next[target]
    emitOwn(next)
  }
}

function add({ target, label }) {
  if (label && label !== target) {
    store.rememberLabel(effectiveRelayId.value, target, label)
    labels.value = { ...labels.value, [target]: label }
  }
  return write(target, newPolicy.value)
}

defineExpose({ reload: loadLive })
</script>

<style scoped>
.policy-editor { display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
.editor-head { display: flex; align-items: baseline; gap: 10px; }
.editor-title { font-weight: 600; }
.editor-note, .editor-muted { color: var(--bs-secondary-color); font-size: 12px; }
.editor-error { color: #f87171; font-size: 12px; }
.policy-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.add-row { position: relative; }
.policy-row, .add-row { display: flex; align-items: center; gap: 6px; flex-wrap: nowrap; min-width: 0; }
.row-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reset-slot { flex: none; width: 14px; }
.field-reset-btn { flex: none; width: 14px; background: none; border: none; padding: 0; font-size: 0.85rem; color: #6c757d; cursor: pointer; line-height: 1; }
.field-reset-btn:hover { color: #dc3545; }
.explain { font-size: 12px; color: var(--bs-secondary-color); }
.explain summary { cursor: pointer; }
.explain ul { margin: 6px 0 4px; padding-left: 18px; }
.explain p { margin: 4px 0 0; }
@media (max-width: 600px) {
  .policy-editor { font-size: 12px; }
  .policy-row, .add-row { gap: 4px; }
}
</style>
