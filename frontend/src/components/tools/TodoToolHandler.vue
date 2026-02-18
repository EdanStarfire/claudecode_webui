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

      <div v-else>
        <ToolSuccessMessage :message="`Task list updated (${todos.length} tasks)`" />
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

// Result (shared composable)
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

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

// Exposed for parent ToolCallCard
const summary = computed(() => `Todo: ${todos.value.length} tasks`)
const params = computed(() => ({ count: todos.value.length, completed: completedCount.value, pending: pendingCount.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.todo-tool-handler {
  font-size: var(--tool-font-size, 13px);
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
  background: var(--tool-bg-header, #f1f5f9);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
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
  border-radius: var(--tool-radius, 4px);
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
  background: var(--tool-bg-header, #f1f5f9);
  color: #495057;
}

.todo-checklist {
  background: white;
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
  overflow: hidden;
}

.todo-item {
  display: flex;
  align-items: start;
  gap: 0.75rem;
  padding: 0.75rem;
  border-bottom: 1px solid var(--tool-bg-header, #f1f5f9);
  transition: background-color 0.15s ease;
}

.todo-item:last-child {
  border-bottom: none;
}

.todo-item:hover {
  background: var(--tool-bg, #f8fafc);
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
  color: var(--tool-text-muted, #64748b);
}

.todo-completed:hover {
  background: var(--tool-bg, #f8fafc);
}

.tool-result {
  padding: 0.75rem;
  border-radius: var(--tool-radius, 4px);
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
}

.tool-code {
  margin: 0;
  padding: 0;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
}
</style>
