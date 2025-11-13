<template>
  <component
    :is="messageComponent"
    :message="message"
    :attachedTools="attachedTools"
  />
</template>

<script setup>
import { computed } from 'vue'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import SystemMessage from './SystemMessage.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true
  },
  attachedTools: {
    type: Array,
    default: () => []
  }
})

// Route to appropriate message component based on type
const messageComponent = computed(() => {
  switch (props.message.type) {
    case 'user':
      return UserMessage
    case 'assistant':
      return AssistantMessage
    case 'system':
      return SystemMessage
    default:
      // Fallback to system message for unknown types
      return SystemMessage
  }
})
</script>

<style scoped>
/* No styles needed - individual message components handle their own layout */
</style>
