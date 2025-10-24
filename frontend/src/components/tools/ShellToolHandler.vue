<template>
  <div class="shell-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="shell-info">
        <span class="shell-icon">{{ toolIcon }}</span>
        <strong>{{ toolLabel }}:</strong>
        <code v-if="shellId" class="shell-id">Shell {{ shellId }}</code>
      </div>

      <!-- BashOutput specific params -->
      <div v-if="toolCall.name === 'BashOutput' && filter" class="filter-info mt-2">
        <strong>Filter:</strong>
        <code class="filter-pattern">{{ filter }}</code>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="toolCall.result" class="tool-section">
      <div class="result-header mb-2">
        <strong>{{ resultLabel }}:</strong>
        <span v-if="shellStatus" class="badge" :class="statusBadgeClass">{{ shellStatus }}</span>
      </div>

      <!-- Output content -->
      <div v-if="outputContent" class="shell-output">
        <pre><code>{{ outputContent }}</code></pre>
      </div>

      <!-- Success/Error message -->
      <div v-else class="tool-result" :class="resultClass">
        <div v-if="toolCall.result.success !== false">
          <i class="bi bi-check-circle"></i>
          {{ resultMessage }}
        </div>
        <div v-else class="text-danger">
          <i class="bi bi-x-circle"></i>
          {{ toolCall.result.error || 'Operation failed' }}
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

const toolIcon = computed(() => {
  if (props.toolCall.name === 'BashOutput') return '📤'
  if (props.toolCall.name === 'KillShell') return '🛑'
  return '🐚'
})

const toolLabel = computed(() => {
  if (props.toolCall.name === 'BashOutput') return 'Reading Shell Output'
  if (props.toolCall.name === 'KillShell') return 'Terminating Shell'
  return 'Shell Operation'
})

const resultLabel = computed(() => {
  if (props.toolCall.name === 'BashOutput') return 'Output'
  if (props.toolCall.name === 'KillShell') return 'Status'
  return 'Result'
})

const shellId = computed(() => {
  return props.toolCall.input?.bash_id || props.toolCall.input?.shell_id
})

const filter = computed(() => {
  return props.toolCall.input?.filter
})

const outputContent = computed(() => {
  if (props.toolCall.name === 'BashOutput') {
    return props.toolCall.result?.output || props.toolCall.result?.stdout
  }
  return null
})

const shellStatus = computed(() => {
  return props.toolCall.result?.status
})

const statusBadgeClass = computed(() => {
  const status = shellStatus.value
  if (status === 'running') return 'bg-success'
  if (status === 'completed') return 'bg-secondary'
  if (status === 'killed' || status === 'terminated') return 'bg-danger'
  return 'bg-info'
})

const resultClass = computed(() => {
  if (props.toolCall.result?.success === false) {
    return 'tool-result-error'
  }
  return 'tool-result-success'
})

const resultMessage = computed(() => {
  if (props.toolCall.result?.message) {
    return props.toolCall.result.message
  }

  if (props.toolCall.name === 'KillShell') {
    return 'Shell terminated successfully'
  }

  return 'Operation completed'
})
</script>

<style scoped>
.shell-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  padding: 0.75rem;
  background: #f8f9fa;
  border-radius: 0.25rem;
}

.shell-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.shell-icon {
  font-size: 1.25rem;
}

.shell-id {
  padding: 0.25rem 0.5rem;
  background: #e9ecef;
  border-radius: 0.25rem;
  font-size: 0.85rem;
  font-family: 'Courier New', monospace;
}

.filter-info {
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.filter-pattern {
  padding: 0.25rem 0.5rem;
  background: #fff3cd;
  border-radius: 0.25rem;
  font-size: 0.85rem;
  font-family: 'Courier New', monospace;
  color: #856404;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
}

.shell-output {
  background: #1e1e1e;
  border-radius: 0.25rem;
  overflow: hidden;
}

.shell-output pre {
  margin: 0;
  padding: 0.75rem;
  max-height: 400px;
  overflow: auto;
  color: #d4d4d4;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  line-height: 1.4;
  white-space: pre;
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
