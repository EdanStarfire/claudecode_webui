<template>
  <div class="messages-area flex-grow-1 overflow-auto p-3" ref="messagesArea">
    <div v-if="displayableMessages.length === 0" class="text-muted text-center py-5">
      No messages yet. Start a conversation!
    </div>

    <div v-for="(message, index) in displayableMessages" :key="index" class="mb-3">
      <!-- TODO: Full message components will be built in Phase 3 -->
      <div
        class="card"
        :class="{
          'bg-light': message.type === 'user',
          'bg-white': message.type === 'assistant',
          'bg-info bg-opacity-10': message.type === 'system'
        }"
      >
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <strong>{{ message.type.toUpperCase() }}</strong>
            <small class="text-muted">{{ formatTimestamp(message.timestamp) }}</small>
          </div>
          <div class="message-content">{{ message.content }}</div>
        </div>
      </div>
    </div>

    <!-- Tool calls (placeholder) -->
    <div v-for="toolCall in currentToolCalls" :key="toolCall.id" class="mb-3">
      <div class="card border-primary">
        <div class="card-body">
          <strong>🔧 {{ toolCall.name }}</strong> - {{ toolCall.status }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'

const messageStore = useMessageStore()
const uiStore = useUIStore()

const messagesArea = ref(null)

const displayableMessages = computed(() => {
  return messageStore.currentMessages.filter(msg => shouldDisplayMessage(msg))
})

const currentToolCalls = computed(() => messageStore.currentToolCalls)

// Auto-scroll on new messages
watch(() => messageStore.currentMessages.length, async () => {
  if (uiStore.autoScrollEnabled) {
    await nextTick()
    if (messagesArea.value) {
      messagesArea.value.scrollTop = messagesArea.value.scrollHeight
    }
  }
})

function shouldDisplayMessage(message) {
  // Basic filtering (full logic will be in Phase 3)
  const subtype = message.subtype || message.metadata?.subtype

  if (message.type === 'system' && subtype === 'init') return false
  if (message.type === 'result') return false
  if (message.type === 'permission_request' || message.type === 'permission_response') return false

  return true
}

function formatTimestamp(timestamp) {
  if (!timestamp) return ''
  return new Date(timestamp).toLocaleTimeString()
}
</script>

<style scoped>
.messages-area {
  scroll-behavior: smooth;
}

.message-content {
  white-space: pre-wrap;
  word-wrap: break-word;
}
</style>
