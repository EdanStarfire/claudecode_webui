<template>
  <div class="kv-list-widget">
    <div
      v-for="(row, idx) in rows"
      :key="idx"
      class="kv-row mb-2"
    >
      <div class="key-wrap">
        <input
          v-model="row.key"
          type="text"
          class="form-control form-control-sm font-monospace"
          placeholder="KEY"
          :disabled="disabled"
          @input="emitUpdate"
        />
        <span
          v-if="isManagedEnvVar(row.key.trim())"
          class="collision-warning"
          title="Overrides an app-managed variable — may change internal behavior."
        >⚠</span>
      </div>
      <input
        v-model="row.value"
        type="text"
        class="form-control form-control-sm font-monospace"
        placeholder="value"
        :disabled="disabled"
        @input="emitUpdate"
      />
      <button
        type="button"
        class="btn btn-outline-danger btn-sm"
        :disabled="disabled"
        @click="removeRow(idx)"
      >×</button>
    </div>

    <button type="button" class="btn btn-outline-secondary btn-sm" :disabled="disabled" @click="addRow">
      + Add variable
    </button>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { isManagedEnvVar } from '@/utils/envVarCollisions.js'

const props = defineProps({
  value: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

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
  emit('update:value', rowsToDict())
}

function addRow() {
  rows.value.push({ key: '', value: '' })
}

function removeRow(idx) {
  rows.value.splice(idx, 1)
  emitUpdate()
}

watch(() => props.value, (val) => {
  rows.value = dictToRows(val)
}, { immediate: true })
</script>

<style scoped>
.kv-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 6px;
  align-items: center;
}

.key-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.key-wrap input {
  flex: 1;
}

.collision-warning {
  position: absolute;
  right: 8px;
  font-size: 0.85rem;
  cursor: help;
  color: #cc8400;
}
</style>
