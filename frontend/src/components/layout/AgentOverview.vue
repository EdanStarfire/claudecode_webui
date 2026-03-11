<template>
  <div class="agent-overview" v-if="session">
    <!-- Agent Identity -->
    <div class="overview-identity">
      <div class="overview-avatar" :style="{ background: avatarColor }">
        {{ avatarLetter }}
      </div>
      <div class="overview-info">
        <div class="overview-name">{{ session.name || 'Agent' }}</div>
        <div class="overview-role" v-if="session.role">{{ session.role }}</div>
        <span v-if="isArchiveMode" class="overview-status-badge status-archived" role="status" aria-label="Archived">ARCHIVED</span>
        <span v-else class="overview-status-badge" :class="statusClass" role="status" :aria-label="`Agent status: ${statusLabel}`">{{ statusLabel }}</span>
      </div>
    </div>

    <!-- Archive Navigation (always visible) -->
    <div class="archive-nav">
      <button
        class="btn-overview"
        :disabled="!hasPrevArchive"
        @click="goToPrevArchive"
        title="Previous archive"
      >
        Prev
      </button>
      <span v-if="isArchiveMode" class="archive-nav-label">{{ archiveIndex + 1 }} / {{ archives.length }}</span>
      <span v-else class="archive-nav-label">{{ archives.length }} archive{{ archives.length !== 1 ? 's' : '' }}</span>
      <button
        class="btn-overview"
        :disabled="!hasNextArchive"
        @click="goToNextArchive"
        title="Next archive"
      >
        Next
      </button>
      <button class="btn-overview btn-overview-primary" :disabled="isArchiveMode ? false : archives.length === 0" @click="isArchiveMode ? jumpToActive() : viewLatestArchive()" :title="isArchiveMode ? (isDeletedAgent ? 'Jump to latest archive' : 'Jump to active session') : 'View latest archive'">
        {{ isArchiveMode ? (isDeletedAgent ? 'Latest' : 'Active') : 'View' }}
      </button>
    </div>

    <!-- Stats Grid -->
    <div class="overview-stats">
      <div class="stat-item">
        <span class="stat-value">{{ messageCount }}</span>
        <span class="stat-label">Messages</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ toolCallCount }}</span>
        <span class="stat-label">Tool Calls</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ childCount }}</span>
        <span class="stat-label">Children</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ uptime }}</span>
        <span class="stat-label">Uptime</span>
      </div>
    </div>

    <!-- Action Buttons (hidden in archive mode) -->
    <div v-if="!isArchiveMode" class="overview-actions">
      <button class="btn-overview" @click="showInfo" title="View session details" aria-label="View session details">
        Info
      </button>
      <button class="btn-overview" @click="editSession" title="Edit session settings" aria-label="Edit session settings">
        Edit
      </button>
      <button class="btn-overview" @click="manageSession" title="Manage session lifecycle" aria-label="Manage session lifecycle">
        Manage
      </button>
    </div>
  </div>
  <div class="agent-overview agent-overview-empty" v-else>
    <span class="empty-text">No session selected</span>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'
import { apiGet } from '@/utils/api'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const uiStore = useUIStore()

const archives = ref([])

const now = ref(Date.now())
let interval = null

onMounted(() => {
  interval = setInterval(() => { now.value = Date.now() }, 10000)
})

onUnmounted(() => {
  if (interval) clearInterval(interval)
})

const session = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return null
  const sess = sessionStore.getSession(id)
  if (sess) return sess
  // Fall back to ghost agent data for deleted agents
  const ghost = sessionStore.ghostAgents.get(id)
  if (ghost) {
    return { session_id: id, name: ghost.name, role: ghost.role, state: 'terminated' }
  }
  return null
})

const avatarLetter = computed(() => {
  const name = session.value?.name || 'A'
  return name.charAt(0).toUpperCase()
})

const avatarColor = computed(() => {
  const name = session.value?.name || ''
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  const hue = Math.abs(hash % 360)
  return `hsl(${hue}, 60%, 55%)`
})

const statusClass = computed(() => {
  if (!session.value) return 'status-none'
  const state = session.value.state
  if (state === 'active' && session.value.is_processing) return 'status-active'
  if (state === 'active') return 'status-idle'
  if (state === 'paused') return 'status-waiting'
  if (state === 'error') return 'status-error'
  if (state === 'terminated') return 'status-terminated'
  return 'status-none'
})

const statusLabel = computed(() => {
  if (!session.value) return 'None'
  const state = session.value.state
  if (state === 'active' && session.value.is_processing) return 'Processing'
  if (state === 'active') return 'Idle'
  if (state === 'paused') return 'Paused'
  if (state === 'error') return 'Error'
  if (state === 'terminated') return 'Terminated'
  if (state === 'created') return 'Created'
  if (state === 'starting') return 'Starting'
  return state
})

const messageCount = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return 0
  const msgs = messageStore.messagesBySession.get(id)
  if (!msgs) return 0
  return msgs.filter(m => m.type === 'user' || m.type === 'assistant').length
})

const toolCallCount = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return 0
  const tools = messageStore.toolCallsBySession.get(id)
  return tools ? tools.length : 0
})

const childCount = computed(() => {
  if (!session.value) return 0
  return session.value.child_minion_ids?.length || 0
})

const uptime = computed(() => {
  const id = sessionStore.currentSessionId
  if (!id) return '--'
  const launchTs = messageStore.launchTimestampBySession.get(id)
  if (!launchTs) return '--'
  const startMs = launchTs * 1000
  const _ = now.value // trigger reactivity
  let endMs
  const state = session.value?.state
  if (state === 'terminated' || state === 'error') {
    // Frozen duration: use updated_at as termination time
    endMs = session.value?.updated_at
      ? new Date(session.value.updated_at).getTime()
      : Date.now()
  } else {
    endMs = Date.now()
  }
  const diff = endMs - startMs
  if (diff < 0) return '--'
  if (diff < 60000) return '< 1m'
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
})

const isArchiveMode = computed(() => !!route.params.archiveId)
const currentArchiveId = computed(() => route.params.archiveId)

const archiveIndex = computed(() => {
  if (!currentArchiveId.value) return -1
  return archives.value.findIndex(a => a.archive_id === currentArchiveId.value)
})

const hasPrevArchive = computed(() => {
  if (!isArchiveMode.value) return archives.value.length > 0
  return archiveIndex.value > 0
})
const hasNextArchive = computed(() => {
  if (!isArchiveMode.value) return false
  return archiveIndex.value < archives.value.length - 1
})

async function fetchArchives() {
  const id = sessionStore.currentSessionId
  if (!id) return
  const sess = sessionStore.getSession(id)
  const ghost = sessionStore.ghostAgents.get(id)
  const pid = sess?.project_id || ghost?.projectId
  if (!pid) return

  try {
    const data = await apiGet(`/api/projects/${pid}/archives/${id}`)
    archives.value = data.archives || []
  } catch {
    archives.value = []
  }
}

watch(
  [() => sessionStore.currentSessionId, () => session.value?.project_id],
  ([newId], [oldId]) => {
    if (newId !== oldId) archives.value = []
    fetchArchives()
  },
  { immediate: true }
)

// Issue #599: Re-fetch archives when a session reset creates new archives
watch(
  () => sessionStore.sessionResets.get(sessionStore.currentSessionId),
  (newVal) => {
    if (newVal !== undefined) fetchArchives()
  }
)

// Issue #691: Re-fetch archives when archives are erased via manage modal
watch(
  () => sessionStore.archiveChanges.get(sessionStore.currentSessionId),
  (newVal) => {
    if (newVal !== undefined) fetchArchives()
  }
)

const isDeletedAgent = computed(() => {
  if (route.params.agentId) return true
  if (sessionStore.ghostAgents.get(sessionStore.currentSessionId)) return true
  // Ephemeral sessions (schedule-owned) behave like deleted agents for navigation
  const sess = sessionStore.getSession(sessionStore.currentSessionId)
  if (sess?.is_ephemeral) return true
  return false
})

function archiveRoute(sid, archiveId) {
  if (isDeletedAgent.value) {
    return `/archive/agent/${sid}/${archiveId}`
  }
  return `/session/${sid}/archive/${archiveId}`
}

function viewLatestArchive() {
  if (archives.value.length === 0) return
  const latest = archives.value[archives.value.length - 1]
  const sid = sessionStore.currentSessionId
  router.push(archiveRoute(sid, latest.archive_id))
}

function goToPrevArchive() {
  if (!hasPrevArchive.value) return
  const sid = route.params.sessionId || route.params.agentId || sessionStore.currentSessionId
  if (!isArchiveMode.value) {
    // From active view, "Prev" enters the latest archive
    const latest = archives.value[archives.value.length - 1]
    router.push(archiveRoute(sid, latest.archive_id))
    return
  }
  const prev = archives.value[archiveIndex.value - 1]
  router.push(archiveRoute(sid, prev.archive_id))
}

function goToNextArchive() {
  if (!hasNextArchive.value) return
  const next = archives.value[archiveIndex.value + 1]
  const sid = route.params.sessionId || route.params.agentId
  router.push(archiveRoute(sid, next.archive_id))
}

function jumpToActive() {
  const sid = route.params.sessionId || route.params.agentId
  const sess = sessionStore.getSession(sid)
  // For deleted or ephemeral agents, go to latest archive instead of live session
  const shouldShowArchive = !sess || sess.is_ephemeral
  if (shouldShowArchive && archives.value.length > 0) {
    const latest = archives.value[archives.value.length - 1]
    router.push(archiveRoute(sid, latest.archive_id))
    return
  }
  router.push(`/session/${sid}`)
}

function editSession() {
  if (session.value) {
    uiStore.showModal('edit-session', { session: session.value })
  }
}

function manageSession() {
  if (session.value) {
    uiStore.showModal('manage-session', { session: session.value })
  }
}

function showInfo() {
  if (session.value) {
    uiStore.showModal('session-info', { sessionId: session.value.session_id })
  }
}
</script>

<style scoped>
.agent-overview {
  padding: 12px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.agent-overview-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.empty-text {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}

/* Identity Row */
.overview-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.overview-avatar {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: white;
  flex-shrink: 0;
}

.overview-info {
  min-width: 0;
}

.overview-name {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.overview-role {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 2px;
}

.overview-status-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 10px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.status-active { background: #ede9fe; color: #5b21b6; }
.status-idle { background: #dcfce7; color: #166534; }
.status-waiting { background: #ffedd5; color: #9a3412; }
.status-error { background: #fee2e2; color: #991b1b; }
.status-terminated { background: #f1f5f9; color: #475569; }
.status-none { background: #f1f5f9; color: #94a3b8; }

/* Stats Grid */
.overview-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
  margin-bottom: 10px;
}

.stat-item {
  text-align: center;
  padding: 4px 2px;
  background: #f8fafc;
  border-radius: 4px;
}

.stat-value {
  display: block;
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
}

.stat-label {
  display: block;
  font-size: 9px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

/* Action Buttons */
.overview-actions {
  display: flex;
  gap: 6px;
}

.btn-overview {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: white;
  color: #475569;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  text-align: center;
}

.btn-overview:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.btn-overview:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-overview-primary {
  background: #eff6ff;
  border-color: #3b82f6;
  color: #1d4ed8;
}

.status-archived { background: #fef3cd; color: #664d03; }

/* Archive Navigation */
.archive-nav {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}

.archive-nav-label {
  font-size: 11px;
  color: #64748b;
  flex: 1;
  text-align: center;
}


</style>
