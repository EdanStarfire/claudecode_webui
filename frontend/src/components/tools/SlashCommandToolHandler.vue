<template>
  <div class="slashcommand-tool-handler">
    <!-- Command Content Section (collapsible) -->
    <div v-if="hasCommandContent" class="tool-section">
      <div class="command-content-header" @click="toggleContentExpanded">
        <div class="d-flex align-items-center gap-2">
          <i class="bi" :class="isContentExpanded ? 'bi-chevron-down' : 'bi-chevron-right'"></i>
          <strong>Command Content</strong>
          <span class="text-muted small">({{ commandContentLineCount }} lines)</span>
        </div>
      </div>

      <div v-if="isContentExpanded" class="command-content-body">
        <pre class="command-content-text">{{ commandContent }}</pre>
      </div>
    </div>

    <!-- Result Section (for errors or non-standard results) -->
    <div v-if="hasError" class="tool-section mt-3">
      <div class="tool-section-label text-danger">
        <i class="bi bi-x-circle"></i>
        Error:
      </div>
      <div class="tool-result tool-result-error">
        <pre class="tool-code">{{ errorMessage }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// Collapsible content state
const isContentExpanded = ref(false)

function toggleContentExpanded() {
  isContentExpanded.value = !isContentExpanded.value
}

// Extract command name and arguments from input
const commandName = computed(() => {
  return props.toolCall.input?.command || 'Unknown Command'
})

// Determine command status
const statusText = computed(() => {
  switch (props.toolCall.status) {
    case 'pending':
      return 'Pending'
    case 'permission_required':
      return 'Awaiting Permission'
    case 'executing':
      return 'Launching'
    case 'completed':
      if (props.toolCall.result?.error) return 'Error'
      return 'Running'
    case 'error':
      return 'Error'
    default:
      return 'Unknown'
  }
})

const statusBadgeClass = computed(() => {
  switch (props.toolCall.status) {
    case 'pending':
      return 'badge-secondary'
    case 'permission_required':
      return 'badge-warning'
    case 'executing':
      return 'badge-primary'
    case 'completed':
      return props.toolCall.result?.error ? 'badge-danger' : 'badge-success'
    case 'error':
      return 'badge-danger'
    default:
      return 'badge-secondary'
  }
})

// Find slash command-related messages by looking for messages after tool result
const commandMessages = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return { running: null, content: null }

  const allMessages = messageStore.messagesBySession.get(sessionId) || []

  // Find the tool result message for this slash command invocation
  const toolResultIndex = allMessages.findIndex(msg =>
    msg.type === 'user' &&
    msg.metadata?.has_tool_results &&
    msg.metadata?.tool_results?.some(r => r.tool_use_id === props.toolCall.id)
  )

  if (toolResultIndex === -1) {
    return { running: null, content: null }
  }

  // Look for the two user messages that follow the tool result
  let runningMessage = null
  let contentMessage = null

  for (let i = toolResultIndex + 1; i < Math.min(toolResultIndex + 3, allMessages.length); i++) {
    const msg = allMessages[i]
    if (msg.type !== 'user') continue

    const content = msg.content || ''

    // First slash command message: contains <command-message>, <command-name>, and <command-args> tags
    if (content.includes('<command-message>') &&
        content.includes('<command-name>') &&
        content.includes('<command-args>')) {
      runningMessage = msg
    }
    // Second slash command message: contains command content with ARGUMENTS: trailer
    else if (content.includes('ARGUMENTS:')) {
      contentMessage = msg
    }
  }

  return { running: runningMessage, content: contentMessage }
})

// Extract arguments from command tags
const commandArguments = computed(() => {
  const runningMsg = commandMessages.value.running
  if (!runningMsg) return null

  const content = runningMsg.content || ''
  // Match <command-args>...</command-args>
  const match = content.match(/<command-args>(.+?)<\/command-args>/s)
  if (match && match[1]) {
    return match[1].trim()
  }

  return null
})

// Extract command content (everything before ARGUMENTS: trailer)
const commandContent = computed(() => {
  const contentMsg = commandMessages.value.content
  if (!contentMsg) return ''

  const content = contentMsg.content || ''

  // Find the ARGUMENTS: line and get everything before it
  const argumentsLinePattern = /\nARGUMENTS:.*$/s
  const beforeArguments = content.replace(argumentsLinePattern, '')

  // Also remove the command-message, command-name, command-args tags if present
  const cleanContent = beforeArguments
    .replace(/<command-message>.*?<\/command-message>/s, '')
    .replace(/<command-name>.*?<\/command-name>/s, '')
    .replace(/<command-args>.*?<\/command-args>/s, '')

  return cleanContent.trim()
})

const hasCommandContent = computed(() => {
  return commandContent.value.length > 0
})

const commandContentLineCount = computed(() => {
  if (!commandContent.value) return 0
  return commandContent.value.split('\n').length
})

// Error handling
const hasError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const errorMessage = computed(() => {
  if (!hasError.value) return ''

  const result = props.toolCall.result
  if (result?.message) return result.message
  if (result?.error) return result.error
  return 'An error occurred'
})
</script>

<style scoped>
.slashcommand-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.command-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  flex-wrap: wrap;
}

.command-icon {
  font-size: 1.5rem;
  font-weight: bold;
  flex-shrink: 0;
  color: #0969da;
}

.command-name {
  padding: 0.25rem 0.5rem;
  background: #e7f3ff;
  border-radius: 0.25rem;
  font-size: 0.85rem;
  font-family: 'Courier New', monospace;
  color: #0969da;
  font-weight: 600;
}

.command-status-badge {
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.badge-secondary {
  background: #6c757d;
  color: white;
}

.badge-warning {
  background: #ffc107;
  color: #000;
}

.badge-primary {
  background: #0d6efd;
  color: white;
}

.badge-success {
  background: #198754;
  color: white;
}

.badge-danger {
  background: #dc3545;
  color: white;
}

.arguments-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  flex-wrap: wrap;
}

.arguments-icon {
  font-size: 1.1rem;
  flex-shrink: 0;
}

.arguments-text {
  padding: 0.2rem 0.5rem;
  background: #e9ecef;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  color: #495057;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.command-content-header {
  padding: 0.75rem;
  background: #e9ecef;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem 0.25rem 0 0;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.command-content-header:hover {
  background: #dee2e6;
}

.command-content-body {
  border: 1px solid #dee2e6;
  border-top: none;
  border-radius: 0 0 0.25rem 0.25rem;
  overflow: hidden;
}

.command-content-text {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: #f8f9fa;
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
  max-height: 500px;
  overflow-y: auto;
  line-height: 1.5;
}

.tool-section-label {
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.tool-result {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  overflow: hidden;
}

.tool-result-error {
  background: #fff5f5;
  border-color: #dc3545;
}

.tool-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}
</style>
