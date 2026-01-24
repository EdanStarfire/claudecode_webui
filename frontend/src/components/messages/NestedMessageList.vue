<template>
  <div class="nested-messages">
    <div v-for="message in nestedMessages" :key="message.timestamp" class="nested-message-item">
      <MessageItem :message="message" />
    </div>

    <div v-if="nestedMessages.length === 0" class="no-nested-messages">
      <span class="text-muted">No subagent activity yet...</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import MessageItem from './MessageItem.vue'

const props = defineProps({
  parentToolUseId: {
    type: String,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// Get nested messages for this parent tool use ID
const nestedMessages = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return []

  return messageStore.getNestedMessages(sessionId, props.parentToolUseId)
})
</script>

<style scoped>
.nested-messages {
  background: #ffffff;
  border-top: 1px solid #dee2e6;
}

.nested-message-item {
  padding: 0.75rem;
  padding-left: 2rem; /* Indent nested messages */
  border-left: 3px solid #6f42c1;
  background: #f8f9fa;
  margin-bottom: 0.5rem;
}

.nested-message-item:last-child {
  margin-bottom: 0;
}

.no-nested-messages {
  padding: 1rem 2rem;
  text-align: center;
  font-style: italic;
  color: #6c757d;
}
</style>
