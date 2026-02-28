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

    <!-- Archive Banner -->
    <div v-if="isArchiveMode" class="archive-banner">
      <span class="archive-badge">ARCHIVED</span>
      <span class="archive-label">Read-only archived session</span>
    </div>

    <!-- Messages Area -->
    <div class="d-flex flex-column flex-grow-1 overflow-hidden">
      <MessageList />
    </div>

    <!-- Input Area -->
    <InputArea :is-archived="isArchiveMode" />

    <!-- Session State Status Line (above status bar) -->
    <SessionStateStatusLine v-if="currentSession && !isArchiveMode" :session-id="props.sessionId" />

    <!-- Session Status Bar (at bottom) -->
    <SessionStatusBar v-if="currentSession && !isArchiveMode" :session-id="props.sessionId" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'
import SessionStateStatusLine from './SessionStateStatusLine.vue'
import SessionStatusBar from '../statusbar/SessionStatusBar.vue'
import MessageList from '../messages/MessageList.vue'
import InputArea from '../messages/InputArea.vue'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  },
  archiveId: {
    type: String,
    default: null
  },
  isDeletedAgent: {
    type: Boolean,
    default: false
  }
})

const route = useRoute()
const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const uiStore = useUIStore()

const currentSession = computed(() => sessionStore.currentSession)
const isLoading = computed(() => uiStore.isLoading)
const loadingMessage = computed(() => uiStore.loadingMessage)

const isArchiveMode = computed(() => !!(props.archiveId || route.params.archiveId))
const effectiveArchiveId = computed(() => props.archiveId || route.params.archiveId)

async function loadArchiveMessages() {
  const archiveId = effectiveArchiveId.value
  if (!archiveId) return

  // Find project ID for this session
  const session = sessionStore.getSession(props.sessionId)
  const ghost = sessionStore.ghostAgents.get(props.sessionId)
  const pid = session?.project_id || ghost?.projectId
  if (!pid) return

  uiStore.showLoading('Loading archived session...')
  try {
    const response = await fetch(`/api/projects/${pid}/archives/${props.sessionId}/${archiveId}/messages?limit=1000&offset=0`)
    if (response.ok) {
      const data = await response.json()
      messageStore.setArchiveMessages(props.sessionId, data.messages || [])
    }
  } catch (e) {
    console.error('Failed to load archive messages:', e)
  } finally {
    uiStore.hideLoading()
  }
}

onMounted(async () => {
  if (isArchiveMode.value) {
    // Set currentSessionId so AgentOverview can display for deleted agents
    sessionStore.currentSessionId = props.sessionId
    await loadArchiveMessages()
  } else if (props.sessionId !== sessionStore.currentSessionId) {
    uiStore.showLoading('Loading session...')
    try {
      await sessionStore.selectSession(props.sessionId)
    } finally {
      uiStore.hideLoading()
    }
  }
})

watch([() => props.sessionId, () => effectiveArchiveId.value], async ([newSessionId, newArchiveId], [oldSessionId, oldArchiveId]) => {
  if (newArchiveId) {
    if (newSessionId !== oldSessionId || newArchiveId !== oldArchiveId) {
      // Update currentSessionId so AgentOverview shows the correct agent
      sessionStore.currentSessionId = newSessionId
      await loadArchiveMessages()
    }
  } else if (oldArchiveId && !newArchiveId) {
    // Leaving archive mode → clear archive messages and reload live session
    messageStore.clearArchiveMessages(newSessionId)
    uiStore.showLoading('Loading session...')
    try {
      await sessionStore.selectSession(newSessionId)
    } finally {
      uiStore.hideLoading()
    }
  } else if (newSessionId !== oldSessionId && newSessionId !== sessionStore.currentSessionId) {
    uiStore.showLoading('Loading session...')
    try {
      await sessionStore.selectSession(newSessionId)
    } finally {
      uiStore.hideLoading()
    }
  }
})

onUnmounted(() => {
  if (isArchiveMode.value) {
    messageStore.clearArchiveMessages(props.sessionId)
  }
})
</script>

<style scoped>
.archive-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  background: #fef3cd;
  border-bottom: 1px solid #ffc107;
  flex-shrink: 0;
}

.archive-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  background: #ffc107;
  color: #664d03;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.archive-label {
  font-size: 12px;
  color: #664d03;
}
</style>
