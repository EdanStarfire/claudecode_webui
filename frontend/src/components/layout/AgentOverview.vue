<template>
  <div class="agent-overview" v-if="session">
    <!-- Agent Identity -->
    <div class="overview-identity">
      <div class="overview-avatar" :class="[isArchiveMode ? 'status-archived-sq' : statusClass, { unread: isUnreviewed }]" :aria-label="`Agent status: ${statusLabel}${isUnreviewed ? ' (new since last viewed)' : ''}`"></div>
      <div class="overview-info">
        <div class="overview-name">{{ session.name || 'Agent' }}</div>
        <div class="overview-sdk-title" v-if="session.sdk_generated_name">{{ session.sdk_generated_name }}</div>
        <div class="overview-role" v-if="session.role">{{ session.role }}</div>
      </div>
      <div
        v-if="!isArchiveMode && estimatedCost !== '--'"
        class="overview-cost"
        :title="costTitle"
      >
        <span class="cost-label">Cost</span>
        <span class="cost-value">{{ estimatedCost }}</span>
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

    <!-- Rates unknown notice -->
    <div v-if="!isArchiveMode && session && !ratesKnown && usageStore.currentUsage" class="rates-unknown-notice">
      <RouterLink to="/settings/pricing">Add pricing rates</RouterLink> to estimate cost.
    </div>
  </div>
  <div class="agent-overview agent-overview-empty" v-else>
    <span class="empty-text">No session selected</span>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUsageStore } from '@/stores/usage'
import { apiGet } from '@/utils/api'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const usageStore = useUsageStore()

const archives = ref([])

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

const estimatedCost = computed(() => {
  const usage = usageStore.currentUsage
  if (!usage) return '--'
  const cost = usage.estimated_cost_usd
  if (cost == null || !usage.rates_known) return '~$?'
  return `~$${cost.toFixed(3)}`
})

const ratesKnown = computed(() => {
  const usage = usageStore.currentUsage
  if (!usage) return true
  return !!usage.rates_known
})

const costTitle = computed(() => {
  if (!ratesKnown.value) return 'Estimated cost — pricing rates not configured'
  return 'Estimated session cost (USD)'
})

const isUnreviewed = computed(() =>
  session.value?.session_id ? sessionStore.isUnreviewed(session.value.session_id) : false
)

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


</script>

<style scoped>
.agent-overview {
  padding: 12px;
  border-bottom: 1px solid var(--bs-border-color);
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

/* Status-colored square (large version — no letter) */
.overview-avatar {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  flex-shrink: 0;
}

.overview-avatar.status-active      { background: #8b5cf6; }
.overview-avatar.status-idle        { background: #22c55e; }
.overview-avatar.status-waiting     { background: #f59e0b; }
.overview-avatar.status-error       { background: #ef4444; }
.overview-avatar.status-terminated  { background: #cbd5e1; }
.overview-avatar.status-none        { background: #94a3b8; }
.overview-avatar.status-archived-sq { background: #fbbf24; }

.overview-info {
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.overview-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.overview-sdk-title {
  font-size: 11px;
  color: #64748b;
  font-style: italic;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 1px;
}

.overview-role {
  font-size: 11px;
  color: #94a3b8;
}

.btn-overview {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  background: var(--bs-body-bg);
  color: var(--bs-secondary-color);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  text-align: center;
}

.btn-overview:hover {
  background: var(--bs-secondary-bg);
  border-color: var(--bs-border-color);
}

.btn-overview:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-overview-primary {
  background: rgba(var(--bs-link-color-rgb), 0.15);
  border-color: var(--bs-link-color);
  color: var(--bs-link-color);
}


/* Rates unknown notice */
.rates-unknown-notice {
  font-size: 11px;
  color: #94a3b8;
  margin-bottom: 8px;
  text-align: center;
}

.rates-unknown-notice a {
  color: var(--bs-link-color);
}

/* Cost pill in identity row */
.overview-cost {
  flex-shrink: 0;
  padding: 4px 8px;
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  background: var(--bs-secondary-bg);
  line-height: 1;
  white-space: nowrap;
}

.overview-cost .cost-label {
  display: block;
  font-size: 9px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-bottom: 2px;
}

.overview-cost .cost-value {
  display: block;
  font-size: 12px;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
  color: var(--bs-emphasis-color);
}

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

.overview-avatar.unread { background: var(--color-unread); }
</style>
