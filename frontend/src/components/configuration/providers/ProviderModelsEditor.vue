<template>
  <div class="models-editor">
    <table v-if="rows.length > 0" class="table table-sm mb-2">
      <thead>
        <tr>
          <th>ID</th>
          <th>LiteLLM Model</th>
          <th>Drop Params</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, idx) in rows" :key="idx">
          <td>
            <input
              v-model="row.id"
              type="text"
              class="form-control form-control-sm font-monospace"
              placeholder="model-id"
              @input="emitUpdate"
            />
          </td>
          <td>
            <input
              v-model="row.litellm_model"
              type="text"
              class="form-control form-control-sm font-monospace"
              placeholder="e.g. bedrock/claude-3-5-sonnet"
              @input="emitUpdate"
            />
          </td>
          <td class="text-center">
            <input type="checkbox" v-model="row.drop_params" @change="emitUpdate" />
          </td>
          <td class="text-end">
            <button type="button" class="btn btn-outline-danger btn-sm" @click="removeRow(idx)">×</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-else class="text-muted small mb-2">No models added yet.</div>

    <button type="button" class="btn btn-outline-secondary btn-sm" @click="addRow">
      + Add model
    </button>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue'])

const rows = ref([])

function emitUpdate() {
  emit('update:modelValue', rows.value.map(r => ({ ...r })))
}

function addRow() {
  rows.value.push({ id: '', litellm_model: '', drop_params: false })
}

function removeRow(idx) {
  rows.value.splice(idx, 1)
  emitUpdate()
}

watch(() => props.modelValue, (val) => {
  rows.value = (val || []).map(m => ({ ...m }))
}, { immediate: true })
</script>

<style scoped>
.table th {
  font-size: 0.75rem;
  font-weight: 600;
}
</style>
