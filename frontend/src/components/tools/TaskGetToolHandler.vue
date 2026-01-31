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
import { computed } from 'vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

// Parameters
const taskId = computed(() => props.toolCall.input?.taskId || null)

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
  background: #d1ecf1;
  border: 1px solid #bee5eb;
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

.task-details-result {
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
  color: #6c757d;
}

.tool-result-error {
  padding: 0.5rem 0.75rem;
  background: #fff5f5;
  border: 1px solid #dc3545;
  border-radius: 0 0 0.25rem 0.25rem;
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
