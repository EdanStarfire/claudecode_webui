<template>
  <div class="border-top bg-light">
    <!-- Connection warning banner -->
    <div
      v-if="!isConnected"
      class="alert alert-danger mb-0 py-2 px-3 small d-flex align-items-center gap-2"
      role="alert"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="bi bi-exclamation-triangle-fill flex-shrink-0" viewBox="0 0 16 16">
        <path d="M8.982 1.566a1.13 1.13 0 0 0-1.96 0L.165 13.233c-.457.778.091 1.767.98 1.767h13.713c.889 0 1.438-.99.98-1.767L8.982 1.566zM8 5c.535 0 .954.462.9.995l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 5.995A.905.905 0 0 1 8 5zm.002 6a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/>
      </svg>
      <strong>Disconnected</strong>
      <span class="text-muted">—</span>
      <span>Cannot send messages while reconnecting...</span>
    </div>

    <div class="d-flex gap-2 align-items-end px-2 py-1">
      <textarea
        id="message-input"
        ref="messageTextarea"
        v-model="inputText"
        class="form-control"
        :placeholder="inputPlaceholder"
        :disabled="isStarting || !isConnected"
        rows="1"
        @input="autoResizeTextarea"
        @keydown.enter.exact.prevent="sendMessage"
      ></textarea>

      <button
        v-if="isProcessing"
        class="btn btn-warning"
        title="Stop current processing"
        @click="interruptSession"
      >
        Stop
      </button>

      <button
        v-else
        class="btn btn-primary"
        :disabled="!inputText.trim() || !isConnected || isStarting"
        @click="sendMessage"
      >
        Send
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useWebSocketStore } from '@/stores/websocket'

const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()

const messageTextarea = ref(null)

const inputText = computed({
  get: () => sessionStore.currentInput,
  set: (value) => { sessionStore.currentInput = value }
})

const isProcessing = computed(() => sessionStore.currentSession?.is_processing || false)
const isConnected = computed(() => wsStore.sessionConnected)
const isStarting = computed(() => sessionStore.currentSession?.state === 'starting')

const inputPlaceholder = computed(() => {
  if (isStarting.value) {
    return 'Session is starting...'
  }
  if (!isConnected.value) {
    return 'Waiting for connection...'
  }
  return 'Type your message to Claude Code...'
})

/**
 * Auto-resize textarea based on content (matching CommComposer behavior)
 */
function autoResizeTextarea() {
  const textarea = messageTextarea.value
  if (!textarea) return

  // Reset height to auto to get the correct scrollHeight
  textarea.style.height = 'auto'

  // Set height based on scrollHeight, respecting min and max
  const newHeight = Math.min(textarea.scrollHeight, parseInt(getComputedStyle(textarea).maxHeight))
  textarea.style.height = newHeight + 'px'
}

function sendMessage() {
  if (!inputText.value.trim()) return

  wsStore.sendMessage(inputText.value)
  inputText.value = ''

  // Reset textarea height after sending
  if (messageTextarea.value) {
    messageTextarea.value.style.height = 'auto'
  }
}

function interruptSession() {
  wsStore.interruptSession()
}
</script>

<style scoped>
textarea {
  resize: vertical;
  min-height: calc(1.5em + 0.5rem + 2px); /* 1 row height */
  max-height: calc(9em + 0.5rem + 2px); /* 6 rows height */
}
</style>
