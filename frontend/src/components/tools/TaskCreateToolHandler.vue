<template>
  <div class="task-create-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="task-header">
        <span class="task-icon">📝</span>
        <strong>Create Task</strong>
      </div>

      <div class="task-details p-2">
        <div v-if="subject" class="task-field mb-2">
          <span class="field-label">Subject:</span>
          <span class="field-value subject-value">{{ subject }}</span>
        </div>

        <div v-if="description" class="task-field mb-2">
          <span class="field-label">Description:</span>
          <div class="field-value description-value">{{ description }}</div>
        </div>

        <div v-if="activeForm" class="task-field">
          <span class="field-label">Active Form:</span>
          <span class="field-value active-form-value">{{ activeForm }}</span>
        </div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section mt-2">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else>
        <ToolSuccessMessage :message="resultContent" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'
import ToolSuccessMessage from './ToolSuccessMessage.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

// Parameters
const subject = computed(() => props.toolCall.input?.subject || null)
const description = computed(() => props.toolCall.input?.description || null)
const activeForm = computed(() => props.toolCall.input?.activeForm || null)

// Result (shared composable)
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

// Expose for parent components
const summary = computed(() => `Create Task: ${subject.value || ''}`)
const params = computed(() => ({ subject: subject.value, description: description.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.task-create-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #e8f4fd;
  border: 1px solid #b8daff;
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
}

.task-icon {
  font-size: 1.1rem;
}

.task-details {
  background: #fff;
  border: 1px solid var(--tool-border, #e2e8f0);
  border-top: none;
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
}

.task-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.field-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
  font-size: var(--tool-code-font-size, 11px);
}

.field-value {
  color: var(--tool-text, #334155);
}

.subject-value {
  font-weight: 500;
}

.description-value {
  white-space: pre-wrap;
  word-break: break-word;
}

.active-form-value {
  font-style: italic;
  color: #0d6efd;
}

.tool-result {
  padding: 0.5rem 0.75rem;
  border-radius: var(--tool-radius, 4px);
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
  flex-direction: column;
  align-items: flex-start;
}

.tool-code {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
