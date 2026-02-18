<template>
  <div class="exit-plan-mode-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="plan-info">
        <span class="plan-icon">📋</span>
        <strong>Exiting Plan Mode</strong>
      </div>

      <div v-if="plan" class="plan-content">
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
      <div v-if="toolCall.result.success !== false">
        <ToolSuccessMessage message="Permission mode reverted to default" />
      </div>
      <div v-else class="tool-error" style="padding: var(--tool-padding, 6px 8px);">
        ❗ {{ toolCall.result.error || 'Failed to exit plan mode' }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ToolSuccessMessage from './ToolSuccessMessage.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const plan = computed(() => props.toolCall.input?.plan)

const summary = computed(() => 'Exit Plan Mode')
const params = computed(() => ({ plan: plan.value }))
const result = computed(() => props.toolCall.result || null)

defineExpose({ summary, params, result })
</script>

<style scoped>
.exit-plan-mode-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  padding: var(--tool-padding, 6px 8px);
  background: var(--tool-bg, #f8fafc);
  border-radius: var(--tool-radius, 4px);
}

.plan-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.plan-icon {
  font-size: 1.25rem;
}

.plan-content {
  margin-top: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.plan-header {
  margin-bottom: 0.5rem;
  font-weight: 600;
  color: #0969da;
}

.plan-text {
  padding: var(--tool-padding, 6px 8px);
  background: var(--tool-bg, #f8fafc);
  border-left: 3px solid #0969da;
  border-radius: var(--tool-radius, 4px);
  font-size: var(--tool-font-size, 13px);
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
