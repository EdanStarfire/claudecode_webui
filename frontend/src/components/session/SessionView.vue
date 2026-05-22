<template>
  <div class="d-flex flex-column flex-grow-1 overflow-hidden position-relative">
    <!-- Archive Banner -->
    <div v-if="isArchiveMode" class="archive-banner">
      <span class="archive-badge">ARCHIVED</span>
      <span class="archive-label">Read-only archived session</span>
    </div>

    <!-- Ephemeral Session Banner (schedule-managed, not yet fired) -->
    <div v-if="isEphemeralIdle" class="ephemeral-banner">
      <span class="ephemeral-badge">SCHEDULED</span>
      <span class="ephemeral-label">This agent is managed by a schedule and starts automatically when the schedule fires.</span>
    </div>

    <!-- Messages Area -->
    <div class="d-flex flex-column flex-grow-1 overflow-hidden">
      <MessageList />
    </div>

    <!-- Input Area -->
    <InputArea ref="inputAreaRef" :is-archived="isArchiveMode" />

    <!-- Session State Status Line (above status bar) -->
    <SessionStateStatusLine v-if="currentSession && !isArchiveMode" :session-id="props.sessionId" />

    <!-- Session Status Bar (at bottom) -->
    <SessionStatusBar v-if="currentSession && !isArchiveMode" :session-id="props.sessionId" />
  </div>
</template>

<script>
export default { name: 'SessionView' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onUnmounted, provide, readonly, ref, toRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useResourceStore } from '@/stores/resource'
import { useUIStore } from '@/stores/ui'
import { apiGet } from '@/utils/api'
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

// Provide per-instance session identity so cached descendants read their own session's data.
provide('viewSessionId', readonly(toRef(props, 'sessionId')))
provide('viewArchiveId', readonly(toRef(props, 'archiveId')))

const route = useRoute()
const inputAreaRef = ref(null)
const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const resourceStore = useResourceStore()
const uiStore = useUIStore()

// Per-instance session lookup — safe under KeepAlive (reads this instance's own session).
const currentSession = computed(() => sessionStore.sessions.get(props.sessionId))

function focusInputWhenReady() {
  nextTick(() => inputAreaRef.value?.focusInput())
}

const isArchiveMode = computed(() => !!(props.archiveId || route.params.archiveId))
const effectiveArchiveId = computed(() => props.archiveId || route.params.archiveId)

// Per-instance ephemeral check — reads this instance's session state, not the global current.
const isEphemeralIdle = computed(() => {
  const session = currentSession.value
  return session?.is_ephemeral && !isArchiveMode.value &&
    (session.state === 'created' || session.state === 'terminated')
})

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
    const data = await apiGet(`/api/projects/${pid}/archives/${props.sessionId}/${archiveId}/messages?limit=1000&offset=0`)
    messageStore.setArchiveMessages(props.sessionId, data.messages || [])
    // Load archived resources
    await resourceStore.loadArchiveResources(props.sessionId, pid, archiveId)
  } catch (e) {
    console.error('Failed to load archive messages:', e)
  } finally {
    uiStore.hideLoading()
  }
}

// Runs on first mount AND every KeepAlive reactivation.
// With :key="cacheKey", sessionId and archiveId never change within a single instance,
// so there is no need to watch them — each route change spawns or reactivates a distinct instance.
onActivated(async () => {
  if (isArchiveMode.value) {
    // Set currentSessionId so AgentOverview can display for deleted agents
    sessionStore.currentSessionId = props.sessionId
    sessionStore.lastViewedArchive.set(props.sessionId, effectiveArchiveId.value)
    // Guard: skip reload if archive messages are already in the store from the first visit.
    if (!messageStore.messagesBySession.has(props.sessionId)) {
      await loadArchiveMessages()
    }
  } else if (props.sessionId !== sessionStore.currentSessionId) {
    await sessionStore.selectSession(props.sessionId)
  }
  focusInputWhenReady()
})

// Clear archive data on deactivation so regular-session instances for the same sessionId
// do not inherit stale archive messages from messagesBySession when reactivated.
onDeactivated(() => {
  if (isArchiveMode.value) {
    messageStore.clearArchiveMessages(props.sessionId)
    resourceStore.clearResources(props.sessionId)
    resourceStore.clearArchiveContext(props.sessionId)
  }
})

// Per-instance state watch: currentSession reads sessions.get(props.sessionId) — correct under KeepAlive.
watch(
  () => currentSession.value?.state,
  (newState, oldState) => {
    if (newState === 'active' && (oldState === 'starting' || oldState === 'created')) {
      focusInputWhenReady()
    }
  }
)

// Safety-net cleanup for LRU eviction (onDeactivated already handles the normal navigation case).
onUnmounted(() => {
  if (isArchiveMode.value) {
    messageStore.clearArchiveMessages(props.sessionId)
    resourceStore.clearResources(props.sessionId)
    resourceStore.clearArchiveContext(props.sessionId)
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

.ephemeral-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  background: #d1ecf1;
  border-bottom: 1px solid #bee5eb;
  flex-shrink: 0;
}

.ephemeral-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  background: #17a2b8;
  color: #fff;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.ephemeral-label {
  font-size: 12px;
  color: #0c5460;
}
</style>
