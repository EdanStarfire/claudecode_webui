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
import { computed } from 'vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
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
</script>

<style scoped>
.task-list-tool-handler {
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
  background: #e2e3e5;
  border: 1px solid #d6d8db;
  border-radius: 0.25rem 0.25rem 0 0;
}

.task-icon {
  font-size: 1.1rem;
}

.task-list-result {
  background: #fff;
  border: 1px solid #dee2e6;
  border-top: none;
  border-radius: 0 0 0.25rem 0.25rem;
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
  font-size: 0.85rem;
  flex-shrink: 0;
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
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-top: none;
  border-radius: 0 0 0.25rem 0.25rem;
}

.tool-result-error {
  padding: 0.5rem 0.75rem;
  background: #fff5f5;
  border: 1px solid #dc3545;
  border-radius: 0 0 0.25rem 0.25rem;
}

.raw-result {
  background: #f8f9fa;
}

.tool-code {
  margin: 0;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
}
</style>
