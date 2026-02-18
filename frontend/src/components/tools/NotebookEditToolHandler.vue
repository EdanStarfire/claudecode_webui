<template>
  <div class="notebook-edit-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="notebook-info">
        <span class="notebook-icon">📓</span>
        <strong>Notebook:</strong>
        <code class="file-path">{{ notebookPath }}</code>
      </div>

      <div class="cell-info mt-2">
        <div class="info-row">
          <strong>Cell ID:</strong>
          <code>{{ cellId || 'N/A' }}</code>
        </div>
        <div class="info-row">
          <strong>Cell Type:</strong>
          <span class="badge" :class="cellTypeBadgeClass">{{ cellType }}</span>
        </div>
        <div v-if="editMode" class="info-row">
          <strong>Edit Mode:</strong>
          <span class="badge bg-info">{{ editMode }}</span>
        </div>
      </div>

      <div v-if="newSource" class="source-preview mt-3">
        <div class="preview-header">
          <span class="preview-label">New Source:</span>
          <span class="preview-stats">{{ lineCount }} lines</span>
        </div>
        <pre class="source-code"><code>{{ newSource }}</code></pre>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="toolCall.result" class="tool-section">
      <div v-if="toolCall.result.success !== false">
        <ToolSuccessMessage :message="resultMessage" />
      </div>
      <div v-else class="tool-error" style="padding: var(--tool-padding, 6px 8px);">
        ❗ {{ toolCall.result.error || 'Operation failed' }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ToolSuccessMessage from './ToolSuccessMessage.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const notebookPath = computed(() => {
  return props.toolCall.input?.notebook_path || 'Unknown'
})

const cellId = computed(() => {
  return props.toolCall.input?.cell_id
})

const cellType = computed(() => {
  return props.toolCall.input?.cell_type || 'code'
})

const editMode = computed(() => {
  return props.toolCall.input?.edit_mode || 'replace'
})

const newSource = computed(() => {
  return props.toolCall.input?.new_source || ''
})

const lineCount = computed(() => {
  return newSource.value.split('\n').length
})

const cellTypeBadgeClass = computed(() => {
  return cellType.value === 'code' ? 'bg-primary' : 'bg-secondary'
})

const resultMessage = computed(() => {
  if (props.toolCall.result?.message) {
    return props.toolCall.result.message
  }

  const mode = editMode.value
  if (mode === 'insert') {
    return 'Cell inserted successfully'
  } else if (mode === 'delete') {
    return 'Cell deleted successfully'
  } else {
    return 'Cell updated successfully'
  }
})

const summary = computed(() => `Notebook: ${editMode.value} cell in ${notebookPath.value}`)
const params = computed(() => ({ notebook_path: notebookPath.value, cell_id: cellId.value, edit_mode: editMode.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.notebook-edit-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  padding: 0.75rem;
  background: var(--tool-bg, #f8fafc);
  border-radius: var(--tool-radius, 4px);
}

.notebook-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.notebook-icon {
  font-size: 1.25rem;
}

.file-path {
  padding: 0.25rem 0.5rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-radius: var(--tool-radius, 4px);
  font-size: var(--tool-code-font-size, 11px);
  word-break: break-all;
}

.cell-info {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.info-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.info-row strong {
  min-width: 100px;
}

.source-preview {
  background: white;
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-bottom: 1px solid var(--tool-border, #e2e8f0);
}

.preview-label {
  font-weight: 600;
}

.preview-stats {
  font-size: var(--tool-code-font-size, 11px);
  color: #6c757d;
}

.source-code {
  margin: 0;
  padding: 0.75rem;
  max-height: 300px;
  overflow: auto;
  background: var(--tool-bg, #f8fafc);
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  line-height: 1.4;
  white-space: pre;
}
</style>
