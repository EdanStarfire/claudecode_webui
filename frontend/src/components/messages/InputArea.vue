<template>
  <div class="border-top bg-light">
    <div class="d-flex gap-2 align-items-end px-2 py-1">
      <textarea
        id="message-input"
        v-model="inputText"
        class="form-control"
        placeholder="Type your message to Claude Code..."
        rows="1"
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
        :disabled="!inputText.trim() || !isConnected"
        @click="sendMessage"
      >
        Send
      </button>
    </div>

    <!-- Status Bar (placeholder) -->
    <div class="d-flex justify-content-between align-items-center px-2 py-1 border-top">
      <div class="d-flex gap-2">
        <span class="text-muted small">Permission Mode: {{ permissionMode }}</span>
      </div>
      <div v-if="isProcessing" class="spinner-border spinner-border-sm" role="status">
        <span class="visually-hidden">Processing...</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useWebSocketStore } from '@/stores/websocket'

const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()

const inputText = computed({
  get: () => sessionStore.currentInput,
  set: (value) => { sessionStore.currentInput = value }
})

const isProcessing = computed(() => sessionStore.currentSession?.is_processing || false)
const isConnected = computed(() => wsStore.sessionConnected)
const permissionMode = computed(() => sessionStore.currentSession?.current_permission_mode || 'default')

function sendMessage() {
  if (!inputText.value.trim()) return

  wsStore.sendMessage(inputText.value)
  inputText.value = ''
}

function interruptSession() {
  wsStore.interruptSession()
}
</script>

<style scoped>
textarea {
  resize: vertical;
  min-height: 40px;
  max-height: 200px;
}
</style>
