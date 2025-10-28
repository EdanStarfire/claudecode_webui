<template>
  <div class="todo-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="todo-header">
        <span class="todo-icon">📋</span>
        <strong>Task List:</strong>
        <div class="todo-summary">
          <span v-if="completedCount > 0" class="todo-count todo-count-completed">
            {{ completedCount }} completed
          </span>
          <span v-if="inProgressCount > 0" class="todo-count todo-count-in-progress">
            {{ inProgressCount }} in progress
          </span>
          <span v-if="pendingCount > 0" class="todo-count todo-count-pending">
            {{ pendingCount }} pending
          </span>
        </div>
      </div>

      <div class="todo-checklist">
        <div
          v-for="(todo, index) in todos"
          :key="index"
          class="todo-item"
          :class="`todo-${todo.status}`"
        >
          <span class="todo-checkbox">{{ getCheckboxIcon(todo.status) }}</span>
          <span class="todo-content">{{ todo.content }}</span>
        </div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="tool-result tool-result-success">
        <div class="success-message">
          <span class="success-icon">✅</span>
          <strong>Task list updated ({{ todos.length }} tasks)</strong>
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

// Parameters
const todos = computed(() => {
  return props.toolCall.input?.todos || []
})

const completedCount = computed(() => {
  return todos.value.filter(t => t.status === 'completed').length
})

const inProgressCount = computed(() => {
  return todos.value.filter(t => t.status === 'in_progress').length
})

const pendingCount = computed(() => {
  return todos.value.filter(t => t.status === 'pending').length
})

function getCheckboxIcon(status) {
  const icons = {
    'pending': '☐',
    'in_progress': '◐',
    'completed': '☑'
  }
  return icons[status] || '☐'
}

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
.todo-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.todo-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem 0.25rem 0 0;
  border-bottom: none;
  flex-wrap: wrap;
}

.todo-icon {
  font-size: 1.3rem;
}

.todo-summary {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-left: auto;
}

.todo-count {
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
}

.todo-count-completed {
  background: #d1e7dd;
  color: #0f5132;
}

.todo-count-in-progress {
  background: #cfe2ff;
  color: #084298;
}

.todo-count-pending {
  background: #e9ecef;
  color: #495057;
}

.todo-checklist {
  background: white;
  border: 1px solid #dee2e6;
  border-radius: 0 0 0.25rem 0.25rem;
  overflow: hidden;
}

.todo-item {
  display: flex;
  align-items: start;
  gap: 0.75rem;
  padding: 0.75rem;
  border-bottom: 1px solid #e9ecef;
  transition: background-color 0.15s ease;
}

.todo-item:last-child {
  border-bottom: none;
}

.todo-item:hover {
  background: #f8f9fa;
}

.todo-checkbox {
  font-size: 1.2rem;
  line-height: 1;
  flex-shrink: 0;
}

.todo-content {
  flex: 1;
  line-height: 1.4;
}

.todo-pending {
  opacity: 0.8;
}

.todo-in_progress {
  background: #f0f7ff;
  font-weight: 500;
}

.todo-in_progress:hover {
  background: #e7f1ff;
}

.todo-completed {
  opacity: 0.6;
  text-decoration: line-through;
  color: #6c757d;
}

.todo-completed:hover {
  background: #f8f9fa;
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-success {
  background: #d1e7dd;
  border: 1px solid #badbcc;
}

.tool-result-error {
  background: #fff5f5;
  border: 1px solid #dc3545;
}

.success-message {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #0f5132;
}

.success-icon {
  font-size: 1.2rem;
}

.tool-code {
  margin: 0;
  padding: 0;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
}
</style>
