<template>
  <div class="params-editor">
    <div
      v-for="(row, idx) in rows"
      :key="idx"
      class="param-row mb-2"
    >
      <input
        v-model="row.key"
        type="text"
        class="form-control form-control-sm font-monospace"
        placeholder="key"
        @input="emitUpdate"
      />
      <div class="value-wrap">
        <input
          :ref="el => { el ? valueRefs.value[idx] = el : delete valueRefs.value[idx] }"
          v-model="row.value"
          type="text"
          class="form-control form-control-sm"
          placeholder="value"
          @input="emitUpdate"
        />
        <div class="secret-picker" v-if="showSecretPicker === idx">
          <div class="secret-picker-inner">
            <div class="secret-picker-header">
              <span class="small fw-semibold">Insert secret reference</span>
              <button type="button" class="btn-close btn-close-sm" @click="showSecretPicker = null" />
            </div>
            <div v-if="secretsStore.secrets.length === 0" class="small text-muted p-2">
              No secrets configured.
            </div>
            <button
              v-for="secret in secretsStore.secrets"
              :key="secret.name"
              type="button"
              class="secret-option font-monospace"
              @click="insertSecretRef(idx, secret.name)"
            >
              {{ secret.name }}
            </button>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="btn btn-outline-secondary btn-sm secret-btn"
        title="Insert secret reference"
        @click="toggleSecretPicker(idx)"
      >🔑</button>
      <button
        type="button"
        class="btn btn-outline-danger btn-sm"
        @click="removeRow(idx)"
      >×</button>
    </div>

    <button type="button" class="btn btn-outline-secondary btn-sm" @click="addRow">
      + Add parameter
    </button>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { useSecretsStore } from '@/stores/secrets'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue'])

const secretsStore = useSecretsStore()
const showSecretPicker = ref(null)
const valueRefs = ref({})

const rows = ref([])

function dictToRows(dict) {
  return Object.entries(dict || {}).map(([key, value]) => ({ key, value }))
}

function rowsToDict() {
  const result = {}
  for (const row of rows.value) {
    if (row.key.trim()) {
      result[row.key.trim()] = row.value
    }
  }
  return result
}

function emitUpdate() {
  emit('update:modelValue', rowsToDict())
}

function addRow() {
  rows.value.push({ key: '', value: '' })
}

function removeRow(idx) {
  rows.value.splice(idx, 1)
  // Rebuild refs keyed by new indices after splice
  const shifted = {}
  for (const [k, el] of Object.entries(valueRefs.value)) {
    const n = Number(k)
    if (n < idx) shifted[n] = el
    else if (n > idx) shifted[n - 1] = el
  }
  valueRefs.value = shifted
  emitUpdate()
}

function toggleSecretPicker(idx) {
  showSecretPicker.value = showSecretPicker.value === idx ? null : idx
}

function insertSecretRef(idx, secretName) {
  const input = valueRefs.value[idx]
  const ref = `\${secret:${secretName}}`
  if (input) {
    const start = input.selectionStart ?? rows.value[idx].value.length
    const end = input.selectionEnd ?? rows.value[idx].value.length
    rows.value[idx].value =
      rows.value[idx].value.slice(0, start) + ref + rows.value[idx].value.slice(end)
  } else {
    rows.value[idx].value += ref
  }
  showSecretPicker.value = null
  emitUpdate()
}

watch(() => props.modelValue, (val) => {
  rows.value = dictToRows(val)
  valueRefs.value = {}
}, { immediate: true })

onMounted(() => {
  secretsStore.fetchIfEmpty()
})
</script>

<style scoped>
.param-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto auto;
  gap: 6px;
  align-items: center;
}

.value-wrap {
  position: relative;
}

.secret-btn {
  padding: 2px 6px;
  font-size: 0.75rem;
}

.secret-picker {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 100;
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  min-width: 200px;
  max-width: 300px;
}

.secret-picker-inner {
  padding: 6px;
}

.secret-picker-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.secret-option {
  display: block;
  width: 100%;
  padding: 4px 8px;
  background: none;
  border: none;
  text-align: left;
  font-size: 0.8rem;
  color: var(--bs-body-color);
  cursor: pointer;
  border-radius: 4px;
}

.secret-option:hover {
  background: var(--bs-tertiary-bg);
}
</style>
