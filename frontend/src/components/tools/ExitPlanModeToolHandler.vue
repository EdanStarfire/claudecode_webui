<template>
  <div class="exit-plan-mode-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="plan-info">
        <span class="plan-icon">📋</span>
        <strong>Exiting Plan Mode</strong>
      </div>

      <div v-if="plan" class="plan-content mt-2">
        <div class="plan-header">
          <strong>Proposed Plan:</strong>
        </div>
        <div class="plan-text">
          {{ plan }}
        </div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="toolCall.result" class="tool-section">
      <div class="result-header mb-2">
        <strong>Result:</strong>
      </div>
      <div class="tool-result" :class="resultClass">
        <div v-if="toolCall.result.success !== false">
          ✅ Permission mode reverted to default
        </div>
        <div v-else class="text-danger">
          ❗ {{ toolCall.result.error || 'Failed to exit plan mode' }}
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

const plan = computed(() => {
  return props.toolCall.input?.plan
})

const resultClass = computed(() => {
  if (props.toolCall.result?.success === false) {
    return 'tool-result-error'
  }
  return 'tool-result-success'
})
</script>

<style scoped>
.exit-plan-mode-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  padding: 0.75rem;
  background: #f8f9fa;
  border-radius: 0.25rem;
}

.plan-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.plan-icon {
  font-size: 1.25rem;
}

.plan-content {
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.plan-header {
  margin-bottom: 0.5rem;
  font-weight: 600;
  color: #0969da;
}

.plan-text {
  padding: 0.75rem;
  background: #f6f8fa;
  border-left: 3px solid #0969da;
  border-radius: 0.25rem;
  font-size: 0.9rem;
  line-height: 1.6;
  white-space: pre-wrap;
}

.result-header {
  font-weight: 600;
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-success {
  background: #d1e7dd;
  color: #0f5132;
}

.tool-result-error {
  background: #f8d7da;
  color: #842029;
}
</style>
