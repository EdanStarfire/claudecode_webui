<template>
  <div class="timeline-view d-flex flex-column h-100">
    <!-- Timeline Header (at top) -->
    <TimelineHeader :legion-id="legionId" />

    <!-- Timeline Messages -->
    <div ref="messagesArea" class="timeline-messages flex-grow-1 overflow-auto p-3">
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
        <div
          v-for="comm in comms"
          :key="comm.comm_id"
          class="comm-item message-row row py-2 mb-2"
        >
          <div class="col-auto message-speaker text-end pe-3">
            <small class="text-muted">{{ formatTimestamp(comm.timestamp) }}</small>
          </div>
          <div class="col message-content-column">
            <div class="comm-card card">
              <div class="card-body p-2">
                <div class="d-flex justify-content-between align-items-start mb-1">
                  <div>
                    <span class="badge" :class="getCommTypeBadge(comm.comm_type)">
                      {{ comm.comm_type }}
                    </span>
                    <strong class="ms-2">{{ getSenderName(comm) }}</strong>
                    <span class="text-muted mx-1">→</span>
                    <strong :class="{ 'recipient-channel': comm.to_channel_id }">{{ getRecipientName(comm) }}</strong>
                  </div>
                </div>
                <div class="comm-content">
                  {{ comm.content }}
                </div>
              </div>
            </div>
          </div>
        </div>
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
    const minion = sessionStore.sessions.get(comm.from_minion_id)
    return minion?.name || 'Unknown'
  }
  return 'Unknown'
}

/**
 * Get recipient name (to minion, user, or channel)
 */
function getRecipientName(comm) {
  if (comm.to_user) {
    return 'You'
  }
  if (comm.to_minion_id) {
    const minion = sessionStore.sessions.get(comm.to_minion_id)
    return minion?.name || 'Unknown'
  }
  if (comm.to_channel_id) {
    // Prioritize channel name over ID for readability
    if (comm.to_channel_name) {
      return `#${comm.to_channel_name}`
    }
    // Fallback to UUID if name is missing
    return comm.to_channel_id
  }
  return 'All'
}

/**
 * Get badge class for comm type
 */
function getCommTypeBadge(commType) {
  const badgeMap = {
    task: 'bg-primary',
    question: 'bg-info',
    info: 'bg-secondary',
    report: 'bg-success'
  }
  return badgeMap[commType] || 'bg-secondary'
}

/**
 * Format timestamp
 */
function formatTimestamp(timestamp) {
  const date = new Date(timestamp)
  return date.toLocaleTimeString()
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

.comm-card {
  background-color: white;
  border: 1px solid #dee2e6;
  border-radius: 0.375rem;
}

/* Channel names styled distinctly with blue color */
.recipient-channel {
  color: #0d6efd !important;
  font-weight: 500 !important;
}

.comm-content {
  white-space: pre-wrap;
  word-break: break-word;
}

.message-speaker {
  min-width: 80px;
  max-width: 80px;
  font-size: 0.875rem;
  color: #6c757d;
}

.message-content-column {
  flex: 1;
  min-width: 0;
}
</style>
