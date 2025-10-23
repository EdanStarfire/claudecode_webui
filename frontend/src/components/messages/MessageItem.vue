<template>
  <div class="message-item mb-3" :class="`message-${message.type}`">
    <component
      :is="messageComponent"
      :message="message"
    />
  </div>
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
.message-item {
  animation: fadeIn 0.2s ease-in;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(-10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
