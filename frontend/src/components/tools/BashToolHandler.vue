<template>
  <div class="bash-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="bash-header">
        <span class="bash-icon">💻</span>
        <div class="bash-header-content">
          <div v-if="description" class="bash-description">
            <strong>Description:</strong> {{ description }}
          </div>
          <div v-if="hasFlags" class="bash-flags">
            {{ flagsText }}
          </div>
        </div>
      </div>

      <div class="bash-command-section">
        <div class="bash-command-label"><strong>Command:</strong></div>
        <div class="bash-command-content">{{ command }}</div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div class="tool-result" :class="resultClass">
        <strong>Output:</strong>
        <pre v-if="resultContent" class="bash-output">{{ resultContent }}</pre>
        <div v-else class="bash-output-empty">No output</div>
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
const command = computed(() => {
  return props.toolCall.input?.command || ''
})

const description = computed(() => {
  return props.toolCall.input?.description || ''
})

const timeout = computed(() => {
  return props.toolCall.input?.timeout
})

const runInBackground = computed(() => {
  return props.toolCall.input?.run_in_background || false
})

const hasFlags = computed(() => {
  return timeout.value || runInBackground.value
})

const flagsText = computed(() => {
  const flags = []
  if (timeout.value) {
    flags.push(`timeout: ${timeout.value}ms`)
  }
  if (runInBackground.value) {
    flags.push('background')
  }
  return flags.join(', ')
})

// Result (shared composable)
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

const resultClass = computed(() => {
  return isError.value ? 'tool-result-error' : 'tool-result-success'
})

// Expose for parent ToolCallCard
const summary = computed(() => `Bash: ${command.value?.substring(0, 60) || ''}`)
const params = computed(() => ({ command: command.value, description: description.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.bash-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.bash-header {
  display: flex;
  align-items: start;
  gap: 0.75rem;
  padding: 0.75rem;
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
  border-bottom: none;
}

.bash-icon {
  font-size: 1.5rem;
  line-height: 1;
}

.bash-header-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.bash-description {
  color: #495057;
}

.bash-flags {
  font-size: var(--tool-code-font-size, 11px);
  color: #6c757d;
  font-family: 'Courier New', monospace;
  background: var(--tool-bg-header, #f1f5f9);
  padding: 0.25rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  display: inline-block;
  align-self: flex-start;
}

.bash-command-section {
  background: #212529;
  color: #f8f9fa;
  padding: 0.75rem;
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
}

.bash-command-label {
  color: #adb5bd;
  margin-bottom: 0.5rem;
  font-size: var(--tool-code-font-size, 11px);
}

.bash-command-content {
  font-family: 'Courier New', monospace;
  font-size: var(--tool-font-size, 13px);
  white-space: pre;
  overflow-x: auto;
  background: #212529;  /* Override global light blue - use dark terminal background */
  color: #f8f9fa;  /* White/light gray text for readability */
}

.tool-result {
  padding: 0.75rem;
  border-radius: var(--tool-radius, 4px);
}

.tool-result-success {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
}

.bash-output {
  margin: 0.5rem 0 0 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: #212529;
  color: #f8f9fa;
  border: none;
  border-radius: var(--tool-radius, 4px);
  white-space: pre;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
  line-height: 1.4;
}

.bash-output-empty {
  margin-top: 0.5rem;
  padding: 0.5rem;
  color: #6c757d;
  font-style: italic;
  text-align: center;
  background: var(--tool-bg, #f8fafc);
  border-radius: var(--tool-radius, 4px);
}
</style>
