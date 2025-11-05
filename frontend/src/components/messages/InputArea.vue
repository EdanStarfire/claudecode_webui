<template>
  <div class="border-top bg-light">
    <!-- Connection warning banner -->
    <div
      v-if="!isConnected"
      class="alert alert-danger mb-0 py-2 px-3 small d-flex align-items-center gap-2"
      role="alert"
    >
      <span class="flex-shrink-0">❗</span>
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
        @keydown="handleKeyPress"
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useWebSocketStore } from '@/stores/websocket'

const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()

const messageTextarea = ref(null)
const windowWidth = ref(window.innerWidth)

const inputText = computed({
  get: () => sessionStore.currentInput,
  set: (value) => { sessionStore.currentInput = value }
})

const isProcessing = computed(() => sessionStore.currentSession?.is_processing || false)
const isConnected = computed(() => wsStore.sessionConnected)
const isStarting = computed(() => sessionStore.currentSession?.state === 'starting')

// Mobile detection based on viewport width
const isMobile = computed(() => windowWidth.value < 768)

const inputPlaceholder = computed(() => {
  if (isStarting.value) {
    return 'Session is starting...'
  }
  if (!isConnected.value) {
    return 'Waiting for connection...'
  }
  return 'Type your message to Claude Code...'
})

// Update window width on resize
function handleResize() {
  windowWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})

/**
 * Handle Enter key press with mobile-specific behavior
 * Mobile (< 768px): Enter creates new line, Shift+Enter also creates new line
 * Desktop (>= 768px): Enter sends message, Shift+Enter creates new line
 */
function handleKeyPress(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    if (isMobile.value) {
      // Mobile: allow new line (do nothing, default behavior)
      return
    } else {
      // Desktop: send message
      event.preventDefault()
      sendMessage()
    }
  }
}

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
