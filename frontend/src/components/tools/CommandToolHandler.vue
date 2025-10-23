<template>
  <div class="command-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="command-info">
        <span class="command-icon">{{ toolIcon }}</span>
        <strong>{{ toolLabel }}:</strong>
        <code class="command-code">{{ commandName }}</code>
      </div>

      <div v-if="commandPrompt" class="prompt-info mt-2">
        <div class="prompt-header">
          <strong>Expanded Prompt:</strong>
        </div>
        <div class="prompt-content">
          {{ commandPrompt }}
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
          <i class="bi bi-check-circle"></i>
          {{ resultMessage }}
        </div>
        <div v-else class="text-danger">
          <i class="bi bi-x-circle"></i>
          {{ toolCall.result.error || 'Command execution failed' }}
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
  if (props.toolCall.name === 'SlashCommand') return '/'
  if (props.toolCall.name === 'Skill') return '⚡'
  return '🔧'
})

const toolLabel = computed(() => {
  if (props.toolCall.name === 'SlashCommand') return 'Slash Command'
  if (props.toolCall.name === 'Skill') return 'Skill'
  return 'Command'
})

const commandName = computed(() => {
  return props.toolCall.input?.command || 'Unknown'
})

const commandPrompt = computed(() => {
  // The result might contain the expanded prompt/description
  return props.toolCall.result?.prompt || props.toolCall.result?.description
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

  if (props.toolCall.name === 'SlashCommand') {
    return 'Slash command executed successfully'
  }
  if (props.toolCall.name === 'Skill') {
    return 'Skill executed successfully'
  }

  return 'Command completed'
})
</script>

<style scoped>
.command-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  padding: 0.75rem;
  background: #f8f9fa;
  border-radius: 0.25rem;
}

.command-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.command-icon {
  font-size: 1.25rem;
}

.command-code {
  padding: 0.25rem 0.5rem;
  background: #e7f3ff;
  border-radius: 0.25rem;
  font-size: 0.85rem;
  font-family: 'Courier New', monospace;
  color: #0969da;
  font-weight: 600;
}

.prompt-info {
  padding: 0.5rem;
  background: white;
  border-radius: 0.25rem;
}

.prompt-header {
  margin-bottom: 0.5rem;
  font-weight: 600;
}

.prompt-content {
  padding: 0.5rem;
  background: #f8f9fa;
  border-left: 3px solid #0969da;
  border-radius: 0.25rem;
  font-size: 0.9rem;
  line-height: 1.5;
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
