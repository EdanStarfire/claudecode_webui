<template>
  <div class="task-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="task-header">
        <span class="task-icon">🤖</span>
        <strong>Agent Task:</strong>
        <span v-if="subagentType" class="agent-type-badge">{{ subagentType }}</span>
      </div>

      <div v-if="description" class="task-description">
        <div class="description-label">Description:</div>
        <div class="description-content">{{ description }}</div>
      </div>

      <div v-if="prompt" class="task-prompt-container">
        <div class="prompt-header">
          <span class="prompt-label">Task Prompt:</span>
          <span class="prompt-length">{{ prompt.length }} characters</span>
        </div>
        <pre class="prompt-code">{{ prompt }}</pre>
      </div>
    </div>

    <!-- Nested Subagent Activities (Issue #195) -->
    <div v-if="hasNestedActivities" class="tool-section nested-activities-section">
      <div class="nested-header" @click="toggleExpanded">
        <span class="toggle-icon">{{ isExpanded ? '▼' : '▶' }}</span>
        <strong>Subagent Activity:</strong>
        <span class="activity-badge">{{ nestedActivityCount }} {{ nestedActivityCount === 1 ? 'message' : 'messages' }}</span>
      </div>

      <div v-if="isExpanded" class="nested-content">
        <NestedMessageList :parentToolUseId="toolCall.id" />
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="agent-output">
        <div class="output-header">
          <span class="output-label">Agent Output:</span>
          <span v-if="resultLines > 0" class="output-count-badge">{{ resultLines }} lines</span>
        </div>
        <div class="output-content">
          <pre class="tool-code">{{ resultContent }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import NestedMessageList from '../messages/NestedMessageList.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// Nested activities state
const isExpanded = ref(false)

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}

// Parameters
const description = computed(() => {
  return props.toolCall.input?.description || null
})

const prompt = computed(() => {
  return props.toolCall.input?.prompt || null
})

const subagentType = computed(() => {
  return props.toolCall.input?.subagent_type || null
})

// Nested activities (Issue #195)
const hasNestedActivities = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId || !props.toolCall.id) return false

  return messageStore.getNestedMessageCount(sessionId, props.toolCall.id) > 0
})

const nestedActivityCount = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId || !props.toolCall.id) return 0

  return messageStore.getNestedMessageCount(sessionId, props.toolCall.id)
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

const resultLines = computed(() => {
  if (!resultContent.value) return 0
  return resultContent.value.split('\n').length
})
</script>

<style scoped>
.task-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.task-header {
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

.task-icon {
  font-size: 1.3rem;
  flex-shrink: 0;
}

.agent-type-badge {
  background: #6f42c1;
  color: white;
  padding: 0.2rem 0.6rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: lowercase;
  font-family: 'Courier New', monospace;
}

.task-description {
  padding: 0.75rem;
  background: #fff;
  border-left: 1px solid #dee2e6;
  border-right: 1px solid #dee2e6;
}

.description-label {
  font-weight: 600;
  color: #6c757d;
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}

.description-content {
  color: #495057;
  line-height: 1.4;
  font-weight: 500;
}

.task-prompt-container {
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 0 0 0.25rem 0.25rem;
  border-top: none;
  overflow: hidden;
}

.prompt-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #e9ecef;
  border-bottom: 1px solid #dee2e6;
  flex-wrap: wrap;
}

.prompt-label {
  font-weight: 600;
  color: #6c757d;
}

.prompt-length {
  color: #6c757d;
  font-size: 0.85rem;
  font-style: italic;
}

.prompt-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: 300px;
  overflow-y: auto;
  line-height: 1.4;
  color: #495057;
}

.agent-output {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  overflow: hidden;
}

.output-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #e9ecef;
  border-bottom: 1px solid #dee2e6;
  flex-wrap: wrap;
}

.output-label {
  font-weight: 600;
  color: #6c757d;
}

.output-count-badge {
  background: #6f42c1;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
}

.output-content {
  padding: 0;
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-error {
  background: #fff5f5;
  border: 1px solid #dc3545;
}

.tool-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  line-height: 1.4;
}

/* Nested Activities Section (Issue #195) */
.nested-activities-section {
  margin-top: 0.5rem;
  border-left: 3px solid #6f42c1;
  background: #f8f9fa;
  border-radius: 0.25rem;
  overflow: hidden;
}

.nested-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  cursor: pointer;
  user-select: none;
  background: #e9ecef;
  transition: background 0.2s;
}

.nested-header:hover {
  background: #dee2e6;
}

.toggle-icon {
  font-size: 0.8rem;
  color: #6c757d;
  width: 1rem;
  display: inline-block;
}

.activity-badge {
  background: #6f42c1;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
  margin-left: auto;
}

.nested-content {
  border-top: 1px solid #dee2e6;
}
</style>
