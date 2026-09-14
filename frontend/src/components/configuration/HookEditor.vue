<template>
  <div class="hook-editor">
    <div class="row g-2 mb-2">
      <div class="col-8">
        <label class="form-label small mb-1">Hook Name</label>
        <input type="text" class="form-control form-control-sm" v-model="name" placeholder="e.g., Audit Logging" />
      </div>
      <div class="col-4 d-flex align-items-end">
        <div class="form-check form-switch mb-1">
          <input class="form-check-input" type="checkbox" id="hook-config-enabled" v-model="enabled" />
          <label class="form-check-label small" for="hook-config-enabled">Enabled</label>
        </div>
      </div>
    </div>

    <div class="d-flex justify-content-between align-items-center mb-2">
      <span class="small text-muted">
        Each entry can target multiple events at once; one entry expands to one settings.json
        rule per (event, matcher) pair.
      </span>
      <button class="btn btn-outline-primary btn-sm text-nowrap ms-2" @click="addEntry">+ Add Hook Entry</button>
    </div>

    <div v-if="entries.length === 0" class="text-center text-muted small py-3">
      No hook entries yet.
    </div>

    <HookEntryRow
      v-for="(entry, i) in entries"
      :key="entry.id"
      :model-value="entry"
      @update:model-value="v => (entries[i] = v)"
      @remove="entries.splice(i, 1)"
      @drag-start="dragIndex = i"
      @drag-drop="onDrop(i)"
      @drag-end="dragIndex = null"
    />

    <div v-if="formError" class="alert alert-danger py-1 small mb-2">{{ formError }}</div>
    <div v-if="error" class="alert alert-danger py-1 small mb-2">{{ error }}</div>

    <div class="d-flex gap-2 mt-2">
      <button class="btn btn-primary btn-sm" @click="save" :disabled="saving">
        {{ saving ? 'Saving...' : (isEdit ? 'Update' : 'Create') }}
      </button>
      <button class="btn btn-secondary btn-sm" @click="$emit('cancel')">Cancel</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import HookEntryRow from './HookEntryRow.vue'

const props = defineProps({
  config: {
    type: Object,
    default: null,
  },
  saving: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: null,
  },
})

const emit = defineEmits(['save', 'cancel'])

const isEdit = computed(() => !!props.config)

const name = ref(props.config?.name || '')
const enabled = ref(props.config?.enabled ?? true)
const entries = ref((props.config?.hooks || []).map(cloneEntry))

const formError = ref(null)

let _entrySeq = 0
function newEntryId() {
  _entrySeq += 1
  return `new-${Date.now()}-${_entrySeq}`
}

function cloneEntry(entry) {
  return {
    id: entry.id || newEntryId(),
    events: [...(entry.events || [])],
    matcher: entry.matcher || '',
    enabled: entry.enabled !== false,
    type: entry.type === 'http' ? 'http' : 'command',
    command: entry.command || '',
    timeout: entry.timeout ?? null,
    url: entry.url || '',
    headers: entry.headers || null,
    allowed_env_vars: entry.allowed_env_vars || null,
  }
}

function addEntry() {
  entries.value.push(cloneEntry({}))
}

const dragIndex = ref(null)
function onDrop(targetIndex) {
  if (dragIndex.value === null || dragIndex.value === targetIndex) return
  const list = [...entries.value]
  const [moved] = list.splice(dragIndex.value, 1)
  list.splice(targetIndex, 0, moved)
  entries.value = list
  dragIndex.value = null
}

function save() {
  formError.value = null
  if (!name.value.trim()) {
    formError.value = 'Name is required'
    return
  }
  emit('save', {
    name: name.value.trim(),
    enabled: enabled.value,
    hooks: entries.value.map(e => ({
      id: e.id?.startsWith('new-') ? undefined : e.id,
      events: e.events,
      matcher: e.matcher?.trim() || null,
      enabled: e.enabled,
      type: e.type,
      command: e.type === 'command' ? (e.command || null) : null,
      timeout: e.timeout === '' || e.timeout === undefined ? null : e.timeout,
      url: e.type === 'http' ? (e.url || null) : null,
      headers: e.type === 'http' ? e.headers : null,
      allowed_env_vars: e.type === 'http' ? e.allowed_env_vars : null,
    })),
  })
}
</script>

<style scoped>
.hook-editor {
  background: var(--bs-secondary-bg);
  border-radius: 6px;
}
</style>
