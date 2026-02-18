<template>
  <div class="command-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="command-info">
        <span class="command-icon">{{ toolIcon }}</span>
        <strong>{{ toolLabel }}:</strong>
        <code class="command-code">{{ commandName }}</code>
      </div>

      <div v-if="commandPrompt" class="prompt-info">
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
      <div v-if="toolCall.result.success !== false">
        <ToolSuccessMessage :message="resultMessage" />
      </div>
      <div v-else class="tool-error" style="padding: var(--tool-padding, 6px 8px);">
        ❗ {{ toolCall.result.error || 'Command execution failed' }}
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

const commandName = computed(() => props.toolCall.input?.command || 'Unknown')

const commandPrompt = computed(() => {
  return props.toolCall.result?.prompt || props.toolCall.result?.description
})

const resultMessage = computed(() => {
  if (props.toolCall.result?.message) return props.toolCall.result.message
  if (props.toolCall.name === 'SlashCommand') return 'Slash command executed successfully'
  if (props.toolCall.name === 'Skill') return 'Skill executed successfully'
  return 'Command completed'
})

const summary = computed(() => `${toolLabel.value}: ${commandName.value}`)
const params = computed(() => ({ command: commandName.value }))
const result = computed(() => props.toolCall.result || null)

defineExpose({ summary, params, result })
</script>

<style scoped>
.command-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  padding: var(--tool-padding, 6px 8px);
  background: var(--tool-bg, #f8fafc);
  border-radius: var(--tool-radius, 4px);
}

.command-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.command-icon {
  font-size: 1.25rem;
}

.command-code {
  padding: 0.25rem 0.5rem;
  background: #e7f3ff;
  border-radius: var(--tool-radius, 4px);
  font-size: var(--tool-code-font-size, 11px);
  font-family: 'Courier New', monospace;
  color: #0969da;
  font-weight: 600;
}

.prompt-info {
  margin-top: 0.5rem;
  padding: 0.5rem;
  background: white;
  border-radius: var(--tool-radius, 4px);
}

.prompt-header {
  margin-bottom: 0.5rem;
  font-weight: 600;
}

.prompt-content {
  padding: 0.5rem;
  background: var(--tool-bg, #f8fafc);
  border-left: 3px solid #0969da;
  border-radius: var(--tool-radius, 4px);
  font-size: var(--tool-font-size, 13px);
  line-height: 1.5;
  white-space: pre-wrap;
}
</style>
