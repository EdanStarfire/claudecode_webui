<template>
  <div class="task-update-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="task-header">
        <span class="task-icon">✏️</span>
        <strong>Update Task</strong>
        <span v-if="taskId" class="task-id-badge">#{{ taskId }}</span>
      </div>

      <div class="task-details p-2">
        <div v-if="status" class="task-field mb-2">
          <span class="field-label">Status:</span>
          <span class="field-value status-value" :class="statusClass">
            {{ statusIcon }} {{ status }}
          </span>
        </div>

        <div v-if="subject" class="task-field mb-2">
          <span class="field-label">Subject:</span>
          <span class="field-value">{{ subject }}</span>
        </div>

        <div v-if="description" class="task-field mb-2">
          <span class="field-label">Description:</span>
          <div class="field-value description-value">{{ description }}</div>
        </div>

        <div v-if="activeForm" class="task-field mb-2">
          <span class="field-label">Active Form:</span>
          <span class="field-value active-form-value">{{ activeForm }}</span>
        </div>

        <div v-if="owner" class="task-field mb-2">
          <span class="field-label">Owner:</span>
          <span class="field-value">{{ owner }}</span>
        </div>

        <div v-if="addBlockedBy?.length" class="task-field mb-2">
          <span class="field-label">Adding Blocked By:</span>
          <span class="field-value">
            <span v-for="id in addBlockedBy" :key="id" class="badge bg-warning text-dark me-1">#{{ id }}</span>
          </span>
        </div>

        <div v-if="addBlocks?.length" class="task-field">
          <span class="field-label">Adding Blocks:</span>
          <span class="field-value">
            <span v-for="id in addBlocks" :key="id" class="badge bg-info text-dark me-1">#{{ id }}</span>
          </span>
        </div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section mt-2">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="tool-result tool-result-success">
        <span class="result-icon">✅</span>
        <span class="result-text">{{ resultContent }}</span>
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

// Parameters
const taskId = computed(() => props.toolCall.input?.taskId || null)
const status = computed(() => props.toolCall.input?.status || null)
const subject = computed(() => props.toolCall.input?.subject || null)
const description = computed(() => props.toolCall.input?.description || null)
const activeForm = computed(() => props.toolCall.input?.activeForm || null)
const owner = computed(() => props.toolCall.input?.owner || null)
const addBlockedBy = computed(() => props.toolCall.input?.addBlockedBy || [])
const addBlocks = computed(() => props.toolCall.input?.addBlocks || [])

// Status styling
const statusIcon = computed(() => {
  switch (status.value) {
    case 'completed':
      return '✅'
    case 'in_progress':
      return '🔄'
    case 'pending':
      return '⏳'
    case 'deleted':
      return '🗑️'
    default:
      return ''
  }
})

const statusClass = computed(() => {
  switch (status.value) {
    case 'completed':
      return 'status-completed'
    case 'in_progress':
      return 'status-in-progress'
    case 'pending':
      return 'status-pending'
    case 'deleted':
      return 'status-deleted'
    default:
      return ''
  }
})

// Result
const hasResult = computed(() => {
  return props.toolCall.result !== null && props.toolCall.result !== undefined
})

const isError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const resultContent = computed(() => {
  if (!hasResult.value) return ''

  const result = props.toolCall.result
  if (result.content !== undefined) {
    return typeof result.content === 'string'
      ? result.content
      : JSON.stringify(result.content, null, 2)
  }
  if (result.message) {
    return result.message
  }
  return JSON.stringify(result, null, 2)
})
</script>

<style scoped>
.task-update-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 0.2rem;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #fff3cd;
  border: 1px solid #ffc107;
  border-radius: 0.25rem 0.25rem 0 0;
}

.task-icon {
  font-size: 1.1rem;
}

.task-id-badge {
  background: #6c757d;
  color: white;
  padding: 0.1rem 0.4rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-family: 'Courier New', monospace;
}

.task-details {
  background: #fff;
  border: 1px solid #dee2e6;
  border-top: none;
  border-radius: 0 0 0.25rem 0.25rem;
}

.task-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.field-label {
  font-weight: 600;
  color: #6c757d;
  font-size: 0.85rem;
}

.field-value {
  color: #495057;
}

.description-value {
  white-space: pre-wrap;
  word-break: break-word;
}

.active-form-value {
  font-style: italic;
  color: #0d6efd;
}

.status-completed {
  color: #198754;
}

.status-in-progress {
  color: #0d6efd;
}

.status-pending {
  color: #6c757d;
}

.status-deleted {
  color: #dc3545;
}

.tool-result {
  padding: 0.5rem 0.75rem;
  border-radius: 0.25rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tool-result-success {
  background: #d4edda;
  border: 1px solid #c3e6cb;
}

.tool-result-error {
  background: #fff5f5;
  border: 1px solid #dc3545;
  flex-direction: column;
  align-items: flex-start;
}

.result-icon {
  font-size: 1rem;
}

.tool-code {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
