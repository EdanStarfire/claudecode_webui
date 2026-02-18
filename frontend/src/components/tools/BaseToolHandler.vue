<template>
  <div class="base-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="tool-label">Parameters:</div>
      <div class="tool-code-block">
        <pre class="tool-code">{{ formattedInput }}</pre>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div class="tool-label">
        {{ isError ? 'Error:' : 'Result:' }}
      </div>
      <div class="tool-code-block" :class="{ 'tool-error': isError }">
        <pre class="tool-code">{{ resultContent }}</pre>
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

const { hasResult, isError, resultContent, formattedInput } = useToolResult(toRef(props, 'toolCall'))

const summary = computed(() => props.toolCall.name || 'Tool')
const params = computed(() => props.toolCall.input || {})
const result = computed(() => props.toolCall.result || null)

defineExpose({ summary, params, result })
</script>

<style scoped>
.base-tool-handler {
  font-size: var(--tool-font-size, 13px);
}
</style>
