<template>
  <div class="skill-tool-handler">
    <!-- Skill Content Section (collapsible) -->
    <div v-if="hasSkillContent" class="tool-section">
      <div class="skill-content-header" @click="toggleContentExpanded">
        <div class="d-flex align-items-center gap-2">
          <i class="bi" :class="isContentExpanded ? 'bi-chevron-down' : 'bi-chevron-right'"></i>
          <strong>Skill Content</strong>
          <span class="text-muted small">({{ skillContentLineCount }} lines)</span>
        </div>
      </div>

      <div v-if="isContentExpanded" class="skill-content-body">
        <pre class="skill-content-text">{{ skillContent }}</pre>
      </div>
    </div>

    <!-- Result Section (for errors or non-standard results) -->
    <div v-if="hasError" class="tool-section mt-3">
      <div class="tool-section-label text-danger">
        ❗ Error:
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

// Extract skill name from command parameter
const skillName = computed(() => {
  return props.toolCall.input?.command || 'Unknown Skill'
})

// Determine skill status
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

// Find skill-related messages by looking for messages that come after the tool result
const skillMessages = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return { running: null, content: null }

  const allMessages = messageStore.messagesBySession.get(sessionId) || []

  // Find the tool result message for this skill invocation
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

    // First skill message: contains <command-message> tag
    if (content.includes('<command-message>') && content.includes('skill is running')) {
      runningMessage = msg
    }
    // Second skill message: contains skill content
    else if (content.startsWith('Base directory for this skill:')) {
      contentMessage = msg
    }
  }

  return { running: runningMessage, content: contentMessage }
})

// Extract base directory from skill content message
const baseDirectory = computed(() => {
  const contentMsg = skillMessages.value.content
  if (!contentMsg) return null

  const content = contentMsg.content || ''
  // Match "Base directory for this skill: <path>"
  const match = content.match(/Base directory(?:\s+for\s+this\s+skill)?:\s*(.+?)(?:\n|$)/i)
  if (match && match[1]) {
    return match[1].trim()
  }

  return null
})

// Extract skill markdown content (everything after base directory line)
const skillContent = computed(() => {
  const contentMsg = skillMessages.value.content
  if (!contentMsg) return ''

  const content = contentMsg.content || ''

  // Find the base directory line and get everything after it
  const baseDirLinePattern = /Base directory(?:\s+for\s+this\s+skill)?:.*?\n/i
  const afterBaseDir = content.replace(baseDirLinePattern, '')
  return afterBaseDir.trim()
})

const hasSkillContent = computed(() => {
  return skillContent.value.length > 0
})

const skillContentLineCount = computed(() => {
  if (!skillContent.value) return 0
  return skillContent.value.split('\n').length
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

// Expose summary, params, result for ToolCallCard
const summary = computed(() => `Skill: ${skillName.value}`)
const params = computed(() => ({ skill: skillName.value }))
const result = computed(() => props.toolCall.result || null)

defineExpose({ summary, params, result })
</script>

<style scoped>
.skill-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.skill-content-header {
  padding: 0.75rem;
  background: var(--tool-bg-header, #f1f5f9);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.skill-content-header:hover {
  background: var(--tool-border, #e2e8f0);
}

.skill-content-body {
  border: 1px solid var(--tool-border, #e2e8f0);
  border-top: none;
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
  overflow: hidden;
}

.skill-content-text {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: var(--tool-bg, #f8fafc);
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
  line-height: 1.5;
}

.tool-section-label {
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.tool-result {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border-color: var(--tool-error-border, #f87171);
}

.tool-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: transparent;
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
}
</style>
