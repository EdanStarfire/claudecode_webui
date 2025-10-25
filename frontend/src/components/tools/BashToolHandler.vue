<template>
  <div class="bash-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
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
import { computed } from 'vue'

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

// Result
const hasResult = computed(() => {
  return props.toolCall.result !== null && props.toolCall.result !== undefined
})

const isError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const resultClass = computed(() => {
  return isError.value ? 'tool-result-error' : 'tool-result-success'
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
</script>

<style scoped>
.bash-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 1rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.bash-header {
  display: flex;
  align-items: start;
  gap: 0.75rem;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem 0.25rem 0 0;
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
  font-size: 0.85rem;
  color: #6c757d;
  font-family: 'Courier New', monospace;
  background: #e9ecef;
  padding: 0.25rem 0.5rem;
  border-radius: 0.2rem;
  display: inline-block;
  align-self: flex-start;
}

.bash-command-section {
  background: #212529;
  color: #f8f9fa;
  padding: 0.75rem;
  border: 1px solid #dee2e6;
  border-radius: 0 0 0.25rem 0.25rem;
}

.bash-command-label {
  color: #adb5bd;
  margin-bottom: 0.5rem;
  font-size: 0.85rem;
}

.bash-command-content {
  font-family: 'Courier New', monospace;
  font-size: 0.9rem;
  white-space: pre;
  overflow-x: auto;
  background: #212529;  /* Override global light blue - use dark terminal background */
  color: #f8f9fa;  /* White/light gray text for readability */
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-success {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
}

.tool-result-error {
  background: #fff5f5;
  border: 1px solid #dc3545;
}

.bash-output {
  margin: 0.5rem 0 0 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: #212529;
  color: #f8f9fa;
  border: none;
  border-radius: 0.25rem;
  white-space: pre;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  line-height: 1.4;
}

.bash-output-empty {
  margin-top: 0.5rem;
  padding: 0.5rem;
  color: #6c757d;
  font-style: italic;
  text-align: center;
  background: #f8f9fa;
  border-radius: 0.25rem;
}
</style>
