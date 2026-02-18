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
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

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

// Result (shared composable)
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

const resultLines = computed(() => {
  if (!resultContent.value) return 0
  return resultContent.value.split('\n').length
})

// Expose for ToolCallCard
const summary = computed(() => `Task: ${description.value || subagentType.value || 'agent'}`)
const params = computed(() => ({ description: description.value, subagent_type: subagentType.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.task-tool-handler {
  font-size: var(--tool-font-size, 13px);
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
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
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
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: lowercase;
  font-family: 'Courier New', monospace;
}

.task-description {
  padding: 0.75rem;
  background: #fff;
  border-left: 1px solid var(--tool-border, #e2e8f0);
  border-right: 1px solid var(--tool-border, #e2e8f0);
}

.description-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}

.description-content {
  color: var(--tool-text, #334155);
  line-height: 1.4;
  font-weight: 500;
}

.task-prompt-container {
  background: #fff;
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
  border-top: none;
  overflow: hidden;
}

.prompt-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-bottom: 1px solid var(--tool-border, #e2e8f0);
  flex-wrap: wrap;
}

.prompt-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
}

.prompt-length {
  color: var(--tool-text-muted, #64748b);
  font-size: 0.85rem;
  font-style: italic;
}

.prompt-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
  line-height: 1.4;
  color: var(--tool-text, #334155);
}

.agent-output {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.output-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-bottom: 1px solid var(--tool-border, #e2e8f0);
  flex-wrap: wrap;
}

.output-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
}

.output-count-badge {
  background: #6f42c1;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-weight: 600;
}

.output-content {
  padding: 0;
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
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
  line-height: 1.4;
}
</style>
