<template>
  <div class="notebook-edit-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
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
      <div class="result-header mb-2">
        <strong>Result:</strong>
      </div>
      <div class="tool-result" :class="resultClass">
        <div v-if="toolCall.result.success !== false">
          <i class="bi bi-check-circle"></i>
          {{ resultMessage }}
        </div>
        <div v-else class="text-danger">
          <i class="bi bi-x-circle"></i>
          {{ toolCall.result.error || 'Operation failed' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

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

const resultClass = computed(() => {
  if (props.toolCall.result?.success === false) {
    return 'tool-result-error'
  }
  return 'tool-result-success'
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
</script>

<style scoped>
.notebook-edit-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  padding: 0.75rem;
  background: #f8f9fa;
  border-radius: 0.25rem;
}

.notebook-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.notebook-icon {
  font-size: 1.25rem;
}

.file-path {
  padding: 0.25rem 0.5rem;
  background: #e9ecef;
  border-radius: 0.25rem;
  font-size: 0.85rem;
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
  border-radius: 0.25rem;
}

.info-row strong {
  min-width: 100px;
}

.source-preview {
  background: white;
  border-radius: 0.25rem;
  overflow: hidden;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem;
  background: #e9ecef;
  border-bottom: 1px solid #dee2e6;
}

.preview-label {
  font-weight: 600;
}

.preview-stats {
  font-size: 0.85rem;
  color: #6c757d;
}

.source-code {
  margin: 0;
  padding: 0.75rem;
  max-height: 300px;
  overflow: auto;
  background: #f8f9fa;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  line-height: 1.4;
  white-space: pre;
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-success {
  background: #d1e7dd;
  color: #0f5132;
}

.tool-result-error {
  background: #f8d7da;
  color: #842029;
}

.result-header {
  font-weight: 600;
}
</style>
