<template>
  <div class="bc-dropdown bc-session-dropdown">
    <div class="dd-section-label">Sessions in {{ browsingProject?.name || '…' }}</div>

    <div v-if="topLevelSessions.length === 0 && projectGhosts.length === 0" class="dd-empty">
      No sessions yet
    </div>

    <BreadcrumbSessionRow
      v-for="session in topLevelSessions"
      :key="session.session_id"
      :session="session"
      @select="handleSessionSelect"
    />

    <template v-if="projectGhosts.length">
      <div class="dd-section-label dd-section-label-secondary">Recently viewed deleted sessions</div>
      <BreadcrumbGhostRow
        v-for="[agentId, ghost] in projectGhosts"
        :key="'ghost-' + agentId"
        :agentId="agentId"
        :ghost="ghost"
        @select="handleGhostSelect(agentId, ghost)"
        @dismiss="handleGhostDismiss(agentId)"
      />
      <div class="dd-ghost-hint">
        Dashed/faded = deleted. Click to reopen its read-only archive. The × only removes it
        from this shortcut list — it doesn't delete the archive.
      </div>
    </template>

    <div class="dd-footer-action" @click="showCreateSessionModal">+ New session in this project</div>
    <div class="dd-footer-action" @click="showDeletedAgentsModal">Browse all deleted sessions in this project…</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'
import { compareAgents } from '@/utils/agentSort'
import BreadcrumbSessionRow from './BreadcrumbSessionRow.vue'
import BreadcrumbGhostRow from './BreadcrumbGhostRow.vue'

const emit = defineEmits(['close'])

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const uiStore = useUIStore()

const browsingProject = computed(() => {
  const id = uiStore.browsingProjectId
  return id ? projectStore.getProject(id) : null
})

const projectSessions = computed(() => {
  const project = browsingProject.value
  if (!project || !project.session_ids) return []
  const list = project.session_ids
    .map(sid => sessionStore.getSession(sid))
    .filter(s => s && !s.is_ephemeral)
  const mode = uiStore.agentSort
  return list.sort((a, b) => compareAgents(mode, a, b, {
    nameOf: s => s.name,
    orderOf: s => s.order,
    idOf: s => s.session_id
  }))
})

const allChildIds = computed(() => {
  const ids = new Set()
  for (const session of projectSessions.value) {
    if (session.child_minion_ids) {
      for (const cid of session.child_minion_ids) ids.add(cid)
    }
  }
  return ids
})

const topLevelSessions = computed(() =>
  projectSessions.value.filter(s => !allChildIds.value.has(s.session_id))
)

const projectGhosts = computed(() => {
  const pid = uiStore.browsingProjectId
  if (!pid) return []
  return [...sessionStore.ghostAgents.entries()].filter(([, ghost]) => ghost.projectId === pid)
})

function handleSessionSelect(sessionId) {
  const session = sessionStore.getSession(sessionId)
  if (session) {
    uiStore.setBrowsingProject(session.project_id)
  }
  const cachedArchive = sessionStore.lastViewedArchive.get(sessionId)
  if (cachedArchive) {
    router.push(`/session/${sessionId}/archive/${cachedArchive}`)
  } else {
    router.push(`/session/${sessionId}`)
  }
  emit('close')
}

function handleGhostSelect(agentId, ghost) {
  const cachedArchive = sessionStore.lastViewedArchive.get(agentId)
  const archiveId = cachedArchive || ghost.latestArchiveId
  if (archiveId) {
    router.push(`/archive/agent/${agentId}/${archiveId}`)
  }
  emit('close')
}

function handleGhostDismiss(agentId) {
  const isCurrentlyViewing = sessionStore.currentSessionId === agentId
  sessionStore.lastViewedArchive.delete(agentId)
  messageStore.clearArchiveMessages(agentId)
  sessionStore.removeGhostAgent(agentId)
  if (isCurrentlyViewing) {
    sessionStore.currentSessionId = null
    router.push('/')
  }
}

function showCreateSessionModal() {
  const project = browsingProject.value
  if (project) {
    router.push(`/settings/session/__new__/general?project_id=${project.project_id}`)
  }
  emit('close')
}

function showDeletedAgentsModal() {
  const project = browsingProject.value
  if (project) {
    uiStore.showModal('deleted-agents', { project })
  }
  emit('close')
}
</script>

<style scoped>
.bc-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: 360px;
  max-height: 400px;
  overflow-y: auto;
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 8px;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
  z-index: 1030;
  padding: 8px;
}

.dd-section-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--bs-secondary-color);
  padding: 2px 4px 6px;
}

.dd-section-label-secondary {
  margin-top: 6px;
}

.dd-empty {
  padding: 8px;
  font-size: 12px;
  color: var(--bs-secondary-color);
}

.dd-ghost-hint {
  font-size: 10.5px;
  color: var(--bs-secondary-color);
  padding: 2px 8px 6px;
  line-height: 1.5;
}

.dd-footer-action {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  margin-top: 4px;
  border-top: 1px solid var(--bs-border-color);
  font-size: 11.5px;
  color: var(--bs-secondary-color);
  cursor: pointer;
  border-radius: 3px;
}

.dd-footer-action:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-body-color);
}
</style>
