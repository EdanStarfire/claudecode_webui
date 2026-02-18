<template>
  <div class="task-get-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="task-header">
        <span class="task-icon">🔍</span>
        <strong>Get Task</strong>
        <span v-if="taskId" class="task-id-badge">#{{ taskId }}</span>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="task-details-result p-2">
        <!-- Try to parse and display task details -->
        <div v-if="parsedTask" class="parsed-task">
          <div class="task-field mb-2">
            <span class="field-label">Subject:</span>
            <span class="field-value subject-value">{{ parsedTask.subject }}</span>
          </div>

          <div v-if="parsedTask.status" class="task-field mb-2">
            <span class="field-label">Status:</span>
            <span class="field-value" :class="statusClass">
              {{ statusIcon }} {{ parsedTask.status }}
            </span>
          </div>

          <div v-if="parsedTask.description" class="task-field mb-2">
            <span class="field-label">Description:</span>
            <div class="field-value description-value">{{ parsedTask.description }}</div>
          </div>

          <div v-if="parsedTask.owner" class="task-field mb-2">
            <span class="field-label">Owner:</span>
            <span class="field-value">{{ parsedTask.owner }}</span>
          </div>

          <div v-if="parsedTask.blockedBy?.length" class="task-field mb-2">
            <span class="field-label">Blocked By:</span>
            <span class="field-value">
              <span v-for="id in parsedTask.blockedBy" :key="id" class="badge bg-warning text-dark me-1">#{{ id }}</span>
            </span>
          </div>

          <div v-if="parsedTask.blocks?.length" class="task-field">
            <span class="field-label">Blocks:</span>
            <span class="field-value">
              <span v-for="id in parsedTask.blocks" :key="id" class="badge bg-info text-dark me-1">#{{ id }}</span>
            </span>
          </div>
        </div>

        <!-- Fallback to raw content -->
        <div v-else class="raw-result">
          <pre class="tool-code">{{ resultContent }}</pre>
        </div>
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

const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

// Parameters
const taskId = computed(() => props.toolCall.input?.taskId || null)

// Expose summary, params, result for parent ToolCallCard
const summary = computed(() => taskId.value ? `Get Task #${taskId.value}` : 'Get Task')
const params = computed(() => ({ taskId: taskId.value }))
const result = computed(() => props.toolCall.result || null)

defineExpose({ summary, params, result })

// Try to parse task details from result
const parsedTask = computed(() => {
  if (!resultContent.value) return null

  const content = resultContent.value

  // Try to parse structured output
  // Example formats:
  // "Subject: task subject\nDescription: task description\nStatus: in_progress"
  // Or JSON format

  // Try JSON first
  try {
    const parsed = JSON.parse(content)
    if (parsed && typeof parsed === 'object') {
      return {
        subject: parsed.subject || parsed.title || '',
        status: parsed.status || '',
        description: parsed.description || '',
        owner: parsed.owner || '',
        blockedBy: parsed.blockedBy || [],
        blocks: parsed.blocks || []
      }
    }
  } catch (e) {
    // Not JSON, try line-based parsing
  }

  // Try line-based parsing
  const lines = content.split('\n')
  const task = {
    subject: '',
    status: '',
    description: '',
    owner: '',
    blockedBy: [],
    blocks: []
  }

  for (const line of lines) {
    const subjectMatch = line.match(/^Subject:\s*(.+)$/i)
    if (subjectMatch) task.subject = subjectMatch[1].trim()

    const statusMatch = line.match(/^Status:\s*(.+)$/i)
    if (statusMatch) task.status = statusMatch[1].trim()

    const descMatch = line.match(/^Description:\s*(.+)$/i)
    if (descMatch) task.description = descMatch[1].trim()

    const ownerMatch = line.match(/^Owner:\s*(.+)$/i)
    if (ownerMatch) task.owner = ownerMatch[1].trim()
  }

  // Return parsed task if we found at least a subject
  if (task.subject) return task

  return null
})

// Status styling
const statusIcon = computed(() => {
  if (!parsedTask.value) return ''
  switch (parsedTask.value.status.toLowerCase()) {
    case 'completed':
      return '✅'
    case 'in_progress':
      return '🔄'
    case 'pending':
      return '⏳'
    default:
      return ''
  }
})

const statusClass = computed(() => {
  if (!parsedTask.value) return ''
  switch (parsedTask.value.status.toLowerCase()) {
    case 'completed':
      return 'status-completed'
    case 'in_progress':
      return 'status-in-progress'
    case 'pending':
      return 'status-pending'
    default:
      return ''
  }
})
</script>

<style scoped>
.task-get-tool-handler {
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

.task-id-badge {
  background: var(--tool-text-muted, #64748b);
  color: white;
  padding: 0.1rem 0.4rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-family: 'Courier New', monospace;
}

.task-details-result {
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
  color: var(--tool-text-muted, #64748b);
}

.subject-value {
  font-weight: 500;
}

.description-value {
  white-space: pre-wrap;
  word-break: break-word;
}

.status-completed {
  color: #198754;
}

.status-in-progress {
  color: #0d6efd;
}

.status-pending {
  color: var(--tool-text-muted, #64748b);
}

.tool-result-error {
  padding: 0.5rem 0.75rem;
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
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
