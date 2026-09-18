<template>
  <component
    :is="messageComponent"
    :message="message"
    :attachedTools="attachedTools"
    :orphanedPermissionTools="orphanedPermissionTools"
    :mergedMessages="mergedMessages"
    :isMessageIdContinuation="isMessageIdContinuation"
    :hasMessageIdContinuationFollowing="hasMessageIdContinuationFollowing"
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
  },
  orphanedPermissionTools: {
    type: Array,
    default: () => []
  },
  mergedMessages: {
    type: Array,
    default: () => []
  },
  // Issue #1957 (visual grouping, follow-up to #1955): purely presentational — only
  // AssistantMessage.vue reads these; other message types ignore them.
  isMessageIdContinuation: {
    type: Boolean,
    default: false
  },
  hasMessageIdContinuationFollowing: {
    type: Boolean,
    default: false
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
