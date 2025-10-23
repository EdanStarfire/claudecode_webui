<template>
  <div class="d-flex flex-column flex-grow-1 overflow-hidden position-relative">
    <!-- Loading Overlay -->
    <div
      v-if="isLoading"
      class="position-absolute top-0 start-0 w-100 h-100 bg-white bg-opacity-75 d-flex flex-column justify-content-center align-items-center"
      style="z-index: 1000;"
    >
      <div class="spinner-border text-primary mb-3" role="status">
        <span class="visually-hidden">Loading...</span>
      </div>
      <p class="text-secondary">{{ loadingMessage }}</p>
    </div>

    <!-- Session Info Bar -->
    <SessionInfoBar v-if="currentSession" :session="currentSession" />

    <!-- Messages Area -->
    <div class="d-flex flex-column flex-grow-1 overflow-hidden">
      <MessageList />
    </div>

    <!-- Input Area -->
    <InputArea />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import SessionInfoBar from './SessionInfoBar.vue'
import MessageList from '../messages/MessageList.vue'
import InputArea from '../messages/InputArea.vue'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()
const uiStore = useUIStore()

const currentSession = computed(() => sessionStore.currentSession)
const isLoading = computed(() => uiStore.isLoading)
const loadingMessage = computed(() => uiStore.loadingMessage)

onMounted(async () => {
  // Select this session
  if (props.sessionId !== sessionStore.currentSessionId) {
    uiStore.showLoading('Loading session...')
    try {
      await sessionStore.selectSession(props.sessionId)
    } finally {
      uiStore.hideLoading()
    }
  }
})

// Watch for sessionId prop changes (e.g., when switching minions in Spy mode)
watch(() => props.sessionId, async (newSessionId, oldSessionId) => {
  if (newSessionId !== oldSessionId && newSessionId !== sessionStore.currentSessionId) {
    uiStore.showLoading('Loading session...')
    try {
      await sessionStore.selectSession(newSessionId)
    } finally {
      uiStore.hideLoading()
    }
  }
})

onUnmounted(() => {
  // Optionally disconnect session websocket on unmount
  // (Keep it running for now so user can navigate back quickly)
})
</script>
