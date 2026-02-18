<template>
  <div class="task-list-tool-handler">
    <!-- Header Section -->
    <div class="tool-section">
      <div class="task-header">
        <span class="task-icon">📋</span>
        <strong>List Tasks</strong>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="task-list-result">
        <!-- Parse and display tasks if possible -->
        <div v-if="parsedTasks.length > 0" class="parsed-tasks">
          <div
            v-for="(task, index) in parsedTasks"
            :key="index"
            class="task-summary d-flex align-items-center gap-2 p-2 border-bottom"
          >
            <span class="status-icon">{{ task.statusIcon }}</span>
            <span class="task-id text-muted">#{{ task.id }}</span>
            <span class="task-subject flex-grow-1">{{ task.subject }}</span>
            <span v-if="task.owner" class="task-owner badge bg-secondary">{{ task.owner }}</span>
          </div>
        </div>

        <!-- Fallback to raw content -->
        <div v-else class="raw-result p-2">
          <pre class="tool-code">{{ resultContent }}</pre>
        </div>
      </div>
    </div>

    <!-- No tasks message -->
    <div v-else-if="toolCall.status === 'completed'" class="tool-section">
      <div class="no-tasks text-muted text-center py-3">
        <span class="empty-icon">📝</span>
        <p class="mb-0 small">No tasks in the list</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

// Shared result composable
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

// Try to parse task list from result
const parsedTasks = computed(() => {
  if (!resultContent.value) return []

  const tasks = []
  const content = resultContent.value

  // Try to parse lines like "#1. [in_progress] Task subject"
  const lines = content.split('\n')
  for (const line of lines) {
    const match = line.match(/^#?(\d+)\.?\s*\[(\w+)\]\s*(.+)$/)
    if (match) {
      const status = match[2].toLowerCase()
      tasks.push({
        id: match[1],
        status: status,
        subject: match[3].trim(),
        owner: null,
        statusIcon: getStatusIcon(status)
      })
    }
  }

  return tasks
})

function getStatusIcon(status) {
  switch (status) {
    case 'completed':
      return '✅'
    case 'in_progress':
      return '🔄'
    case 'pending':
    default:
      return '⏳'
  }
}

// Expose summary/params/result for ToolCallCard
const summary = computed(() => 'List Tasks')
const params = computed(() => ({}))
const result = computed(() => props.toolCall.result || null)

defineExpose({ summary, params, result })
</script>

<style scoped>
.task-list-tool-handler {
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
  background: var(--tool-bg-header, #f1f5f9);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
}

.task-icon {
  font-size: 1.1rem;
}

.task-list-result {
  background: #fff;
  border: 1px solid var(--tool-border, #e2e8f0);
  border-top: none;
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
}

.task-summary:last-child {
  border-bottom: none !important;
}

.status-icon {
  font-size: 1rem;
  flex-shrink: 0;
}

.task-id {
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  flex-shrink: 0;
  color: var(--tool-text-muted, #64748b);
}

.task-subject {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-icon {
  font-size: 1.5rem;
  display: block;
  margin-bottom: 0.25rem;
}

.no-tasks {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-top: none;
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
}

.tool-result-error {
  padding: 0.5rem 0.75rem;
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
}

.raw-result {
  background: var(--tool-bg, #f8fafc);
}

.tool-code {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
}
</style>
