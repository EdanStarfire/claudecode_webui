<template>
  <div class="agent-strip" :class="{ 'theme-red': uiStore.isRedBackground }" ref="stripEl" v-if="browsingProject" @click="handleStripClick">
    <span class="strip-project-label">{{ browsingProject.name }}</span>
    <span v-if="isBrowsingOther" class="strip-viewing-badge" aria-label="Temporarily viewing this project">VIEWING</span>
    <template v-for="session in topLevelSessions" :key="session.session_id">
      <!-- Stacked chip for parents with children -->
      <StackedChip
        v-if="hasChildren(session)"
        :session="session"
        :isActive="session.session_id === currentSessionId || isChildActive(session)"
        @select="handleChipSelect"
      />
      <!-- Plain chip for standalone sessions -->
      <AgentChip
        v-else
        :session="session"
        :isActive="session.session_id === currentSessionId"
        @select="handleChipSelect"
      />
    </template>
    <!-- Ghost chips for deleted agents (filtered to current project) -->
    <template v-for="[agentId, ghost] in projectGhosts" :key="'ghost-' + agentId">
      <AgentChip
        :session="{ session_id: agentId, agent_id: agentId, name: ghost.name, role: ghost.role }"
        :isGhost="true"
        @select="handleGhostSelect(agentId, ghost)"
        @dismiss="handleGhostDismiss(agentId)"
      />
    </template>

    <button class="strip-add-btn" @click.stop="showCreateSessionModal" title="Add agent" aria-label="Add new agent" data-testid="add-session-button">
      +
    </button>
    <!-- Archive/recovery button -->
    <button
      class="strip-archive-btn"
      @click.stop="showDeletedAgentsModal"
      title="View archived agents"
      aria-label="View archived agents"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm0 14.5A6.5 6.5 0 1 1 8 1.5a6.5 6.5 0 0 1 0 13zM8.5 4H7v5l4.25 2.55.75-1.23L8.5 8.25V4z"/>
      </svg>
    </button>
    <span v-if="topLevelSessions.length === 0 && projectGhosts.length === 0" class="strip-empty">No agents yet</span>
    <button
      class="strip-panel-btn"
      :class="{ 'panel-open': uiStore.rightPanelVisible }"
      @click.stop="uiStore.toggleRightPanel()"
      title="Toggle right panel"
      aria-label="Toggle right panel"
      :aria-expanded="uiStore.rightPanelVisible"
    >☰</button>
  </div>
  <div v-else class="agent-strip agent-strip-empty" :class="{ 'theme-red': uiStore.isRedBackground }">
    <span class="strip-empty">Select a project above</span>
    <button
      class="strip-panel-btn"
      :class="{ 'panel-open': uiStore.rightPanelVisible }"
      @click.stop="uiStore.toggleRightPanel()"
      title="Toggle right panel"
      aria-label="Toggle right panel"
      :aria-expanded="uiStore.rightPanelVisible"
    >☰</button>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import AgentChip from './AgentChip.vue'
import StackedChip from './StackedChip.vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'
import { useRouter } from 'vue-router'

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const scheduleStore = useScheduleStore()
const uiStore = useUIStore()
const router = useRouter()

// Eagerly load schedules so AgentChip badges render immediately
watch(
  () => uiStore.browsingProjectId,
  (projectId) => {
    if (projectId) {
      scheduleStore.loadSchedules(projectId)
    }
  },
  { immediate: true }
)

// Auto-collapse stacks and reset browsing project when clicking outside nav area
const stripEl = ref(null)

// Active project = the project containing the currently selected session
const activeProjectId = computed(() => {
  const session = sessionStore.currentSession
  if (!session) return null
  return session.project_id
})

const isBrowsingOther = computed(() =>
  activeProjectId.value && uiStore.browsingProjectId !== activeProjectId.value
)

function handleDocumentClick(e) {
  const inStrip = stripEl.value && stripEl.value.contains(e.target)
  const inPillBar = e.target.closest('.project-pill-bar')

  if (!inStrip && !inPillBar) {
    uiStore.collapseAllStacks()
    // Reset browsing project back to active project
    if (activeProjectId.value && uiStore.browsingProjectId !== activeProjectId.value) {
      uiStore.setBrowsingProject(activeProjectId.value)
    }
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick, true)
})

onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick, true)
})

const currentSessionId = computed(() => sessionStore.currentSessionId)

const browsingProject = computed(() => {
  const id = uiStore.browsingProjectId
  if (!id) return null
  return projectStore.getProject(id)
})

const projectSessions = computed(() => {
  const project = browsingProject.value
  if (!project || !project.session_ids) return []

  return project.session_ids
    .map(sid => sessionStore.getSession(sid))
    .filter(s => s && !s.is_ephemeral)
    .sort((a, b) => (a.order || 0) - (b.order || 0))
})

// Set of all child IDs so we can exclude them from top-level rendering
const allChildIds = computed(() => {
  const ids = new Set()
  for (const session of projectSessions.value) {
    if (session.child_minion_ids) {
      for (const cid of session.child_minion_ids) {
        ids.add(cid)
      }
    }
  }
  return ids
})

// Only show top-level sessions (not children already shown under a parent)
const topLevelSessions = computed(() => {
  return projectSessions.value.filter(s => !allChildIds.value.has(s.session_id))
})

// Ghost agents filtered to the current browsing project
const projectGhosts = computed(() => {
  const pid = uiStore.browsingProjectId
  if (!pid) return []
  return [...sessionStore.ghostAgents.entries()]
    .filter(([, ghost]) => ghost.projectId === pid)
})

function hasChildren(session) {
  return session.child_minion_ids && session.child_minion_ids.some(id => sessionStore.getSession(id))
}

function isChildActive(session) {
  if (!session.child_minion_ids) return false
  return session.child_minion_ids.includes(currentSessionId.value)
}

function handleChipSelect(sessionId) {
  const session = sessionStore.getSession(sessionId)
  if (session) {
    uiStore.setBrowsingProject(session.project_id)
  }
  // Restore last viewed archive if one was remembered
  const cachedArchive = sessionStore.lastViewedArchive.get(sessionId)
  if (cachedArchive) {
    router.push(`/session/${sessionId}/archive/${cachedArchive}`)
  } else {
    sessionStore.selectSession(sessionId)
    router.push(`/session/${sessionId}`)
  }
}

function handleStripClick(e) {
  // Collapse all expanded stacks when clicking empty strip area
  if (e.target.classList.contains('agent-strip')) {
    uiStore.collapseAllStacks()
  }
}

function showCreateSessionModal() {
  const project = browsingProject.value
  if (project) {
    router.push(`/settings/session/__new__/general?project_id=${project.project_id}`)
  }
}

function showDeletedAgentsModal() {
  const project = browsingProject.value
  if (project) {
    uiStore.showModal('deleted-agents', { project })
  }
}

function handleGhostSelect(agentId, ghost) {
  // Restore last viewed archive or fall back to latest
  const cachedArchive = sessionStore.lastViewedArchive.get(agentId)
  const archiveId = cachedArchive || ghost.latestArchiveId
  if (archiveId) {
    router.push(`/archive/agent/${agentId}/${archiveId}`)
  }
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
</script>

<style scoped>
.agent-strip {
  min-height: 56px;
  background: var(--bs-secondary-bg);
  border-bottom: 1px solid var(--bs-border-color);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 16px;
  overflow-x: auto;
  flex-shrink: 0;
}

.agent-strip::-webkit-scrollbar {
  height: 3px;
}

.agent-strip::-webkit-scrollbar-thumb {
  background: var(--bs-border-color);
  border-radius: 3px;
}

.agent-strip-empty {
  justify-content: center;
}

.strip-project-label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--bs-secondary-color);
  letter-spacing: 0.3px;
  padding-right: 8px;
  flex-shrink: 0;
}

.strip-viewing-badge {
  background: var(--bs-tertiary-bg);
  color: var(--bs-secondary-color);
  font-size: 9px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  flex-shrink: 0;
  letter-spacing: 0.3px;
}

.strip-empty {
  font-size: 12px;
  color: var(--bs-secondary-color);
  font-style: italic;
}

.strip-add-btn {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: 1px dashed var(--bs-border-color);
  background: transparent;
  color: var(--bs-secondary-color);
  font-size: 16px;
  font-weight: 300;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.strip-add-btn:hover {
  border-color: #3b82f6;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.1);
}

.strip-archive-btn {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  border: 1px solid var(--bs-border-color);
  background: transparent;
  color: var(--bs-secondary-color);
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.strip-archive-btn:hover {
  border-color: var(--bs-secondary-color);
  color: var(--bs-body-color);
  background: var(--bs-tertiary-bg);
}

.strip-panel-btn {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left: auto;
  position: sticky;
  right: 0;
}

.strip-panel-btn:hover {
  border-color: var(--bs-secondary-color);
  color: var(--bs-body-color);
  background: var(--bs-tertiary-bg);
}

.strip-panel-btn.panel-open {
  background: rgba(var(--bs-link-color-rgb), 0.2);
  border-color: var(--bs-link-color);
  color: var(--bs-link-color);
}


@media (max-width: 767px) {
  .agent-strip {
    padding: 0 8px;
    gap: 4px;
  }
}
</style>
