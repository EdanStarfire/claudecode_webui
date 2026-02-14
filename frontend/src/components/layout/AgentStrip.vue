<template>
  <div class="agent-strip" :class="{ 'theme-red': uiStore.isRedBackground }" ref="stripEl" v-if="browsingProject" @click="handleStripClick">
    <span class="strip-project-label">{{ browsingProject.name }}</span>
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
    <button class="strip-add-btn" @click.stop="showCreateSessionModal" title="Add agent">
      +
    </button>
    <span v-if="topLevelSessions.length === 0" class="strip-empty">No agents yet</span>
  </div>
  <div v-else class="agent-strip agent-strip-empty" :class="{ 'theme-red': uiStore.isRedBackground }">
    <span class="strip-empty">Select a project above</span>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import AgentChip from './AgentChip.vue'
import StackedChip from './StackedChip.vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useRouter } from 'vue-router'

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const router = useRouter()

// Auto-collapse stacks and reset browsing project when clicking outside nav area
const stripEl = ref(null)

// Active project = the project containing the currently selected session
const activeProjectId = computed(() => {
  const session = sessionStore.currentSession
  if (!session) return null
  return session.project_id
})

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
    .filter(Boolean)
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

function hasChildren(session) {
  return session.child_minion_ids && session.child_minion_ids.length > 0
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
  sessionStore.selectSession(sessionId)
  router.push(`/session/${sessionId}`)
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
    uiStore.showModal('create-minion', { project })
  }
}
</script>

<style scoped>
.agent-strip {
  height: 56px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
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
  background: #cbd5e1;
  border-radius: 3px;
}

.agent-strip-empty {
  justify-content: center;
}

.strip-project-label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  color: #94a3b8;
  letter-spacing: 0.3px;
  padding-right: 8px;
  flex-shrink: 0;
}

.strip-empty {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}

.strip-add-btn {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  border: 1px dashed #cbd5e1;
  background: transparent;
  color: #94a3b8;
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
  background: #eff6ff;
}

.agent-strip.theme-red {
  background: #fef2f2;
  border-bottom-color: #fecaca;
}

@media (max-width: 767px) {
  .agent-strip {
    padding: 0 8px;
    gap: 4px;
  }
}
</style>
