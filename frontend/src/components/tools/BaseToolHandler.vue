<template>
  <div class="base-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="tool-section-label">Parameters:</div>
      <div class="tool-parameters">
        <pre class="tool-code">{{ formattedInput }}</pre>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div class="tool-section-label">
        {{ isError ? 'Error:' : 'Result:' }}
      </div>
      <div class="tool-result" :class="{ 'tool-result-error': isError }">
        <pre class="tool-code">{{ resultContent }}</pre>
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

const formattedInput = computed(() => {
  if (!props.toolCall.input) return '{}'
  return JSON.stringify(props.toolCall.input, null, 2)
})

const hasResult = computed(() => {
  return props.toolCall.result !== null && props.toolCall.result !== undefined
})

const isError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const resultContent = computed(() => {
  if (!hasResult.value) return ''

  const result = props.toolCall.result

  // If result has content property, use that
  if (result.content !== undefined) {
    return typeof result.content === 'string'
      ? result.content
      : JSON.stringify(result.content, null, 2)
  }

  // If result has message property (error case), use that
  if (result.message) {
    return result.message
  }

  // Otherwise stringify the whole result
  return JSON.stringify(result, null, 2)
})
</script>

<style scoped>
.tool-section {
  margin-bottom: 1rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.tool-section-label {
  font-weight: 600;
  color: #6c757d;
  font-size: 0.9rem;
  margin-bottom: 0.5rem;
}

.tool-parameters,
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
  white-space: pre;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}
</style>
