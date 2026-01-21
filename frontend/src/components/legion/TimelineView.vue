<template>
  <div class="timeline-view d-flex flex-column h-100">
    <!-- Timeline Header (at top) -->
    <TimelineHeader :legion-id="legionId" />

    <!-- Timeline Messages -->
    <div ref="messagesArea" class="timeline-messages flex-grow-1 overflow-auto">
      <div v-if="loading" class="text-center text-muted">
        <div class="spinner-border spinner-border-sm me-2" role="status"></div>
        Loading timeline...
      </div>

      <div v-else-if="error" class="text-center text-danger">
        Failed to load timeline: {{ error }}
      </div>

      <div v-else-if="comms.length === 0" class="text-center text-muted">
        No messages yet. Minions will appear here once they start communicating.
      </div>

      <div v-else class="comm-list">
        <CommCard
          v-for="comm in comms"
          :key="comm.comm_id"
          :comm="comm"
          :sender-name="getSenderName(comm)"
          :recipient-name="getRecipientName(comm)"
        />
      </div>
    </div>

    <!-- Comm Composer -->
    <CommComposer :legion-id="legionId" />

    <!-- Status Bar (at bottom) -->
    <TimelineStatusBar :legion-id="legionId" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useLegionStore } from '../../stores/legion'
import { useProjectStore } from '../../stores/project'
import { useSessionStore } from '../../stores/session'
import { useWebSocketStore } from '../../stores/websocket'
import { useUIStore } from '../../stores/ui'
import TimelineHeader from '../header/TimelineHeader.vue'
import TimelineStatusBar from '../statusbar/TimelineStatusBar.vue'
import CommComposer from './CommComposer.vue'
import CommCard from '../common/CommCard.vue'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const legionStore = useLegionStore()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()
const uiStore = useUIStore()

const loading = ref(true)
const error = ref(null)
const messagesArea = ref(null)

// Get legion name
const legion = computed(() => projectStore.projects.get(props.legionId))
const legionName = computed(() => legion.value?.name || 'Legion')

// Get comms for this legion
const comms = computed(() => legionStore.currentComms)

// Get minions for name lookups
const minions = computed(() => legionStore.currentMinions)

/**
 * Get sender name (from minion or user)
 */
function getSenderName(comm) {
  if (comm.from_user) {
    return 'You'
  }
  if (comm.from_minion_id) {
    // Check if system-generated (matches backend SYSTEM_MINION_ID)
    if (comm.from_minion_id === 'ffffffff-ffff-ffff-ffff-ffffffffffff') {
      return 'SYSTEM'
    }
    const minion = sessionStore.sessions.get(comm.from_minion_id)
    return minion?.name || 'Unknown'
  }
  return 'Unknown'
}

/**
 * Get recipient name (to minion or user)
 */
function getRecipientName(comm) {
  if (comm.to_user) {
    return 'You'
  }
  if (comm.to_minion_id) {
    const minion = sessionStore.sessions.get(comm.to_minion_id)
    return minion?.name || 'Unknown'
  }
  return 'All'
}

/**
 * Scroll to bottom (respects autoscroll setting)
 */
async function scrollToBottom() {
  if (uiStore.autoScrollEnabled) {
    await nextTick()
    if (messagesArea.value) {
      messagesArea.value.scrollTop = messagesArea.value.scrollHeight
    }
  }
}

/**
 * Load timeline
 */
async function loadTimeline() {
  loading.value = true
  error.value = null

  try {
    // Set current legion
    legionStore.setCurrentLegion(props.legionId)

    // Load timeline and minions in parallel
    await Promise.all([
      legionStore.loadTimeline(props.legionId),
      legionStore.loadMinions(props.legionId)
    ])

    // Wait for DOM to update, then scroll to bottom
    await nextTick()
    await scrollToBottom()

    // Extra scroll after a short delay to ensure content is fully rendered
    setTimeout(() => {
      scrollToBottom()
    }, 100)
  } catch (err) {
    console.error('Failed to load timeline:', err)
    // Show detailed error information
    if (err.response) {
      error.value = `${err.message} (${err.response.status})`
    } else {
      error.value = err.message || 'Unknown error'
    }
  } finally {
    loading.value = false
  }
}

/**
 * Connect to Legion WebSocket
 */
function connectWebSocket() {
  wsStore.connectLegion(props.legionId)
}

/**
 * Disconnect from Legion WebSocket
 */
function disconnectWebSocket() {
  wsStore.disconnectLegion()
}

// Watch for new comms and auto-scroll
watch(() => comms.value.length, () => {
  scrollToBottom()
})

// Lifecycle
onMounted(() => {
  loadTimeline()
  connectWebSocket()

  // Scroll to bottom after initial render
  setTimeout(() => {
    scrollToBottom()
  }, 100)
})

onUnmounted(() => {
  disconnectWebSocket()
})
</script>

<style scoped>
.timeline-view {
  height: 100%;
}

.timeline-messages {
  background-color: #f8f9fa;
}
</style>
