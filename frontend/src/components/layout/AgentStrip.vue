<template>
  <div class="agent-strip" v-if="browsingProject">
    <span class="strip-project-label">{{ browsingProject.name }}</span>
    <AgentChip
      v-for="session in projectSessions"
      :key="session.session_id"
      :session="session"
      :isActive="session.session_id === currentSessionId"
      @select="handleChipSelect"
    />
    <button class="strip-add-btn" @click="showCreateSessionModal" title="Add agent">
      +
    </button>
    <span v-if="projectSessions.length === 0" class="strip-empty">No agents yet</span>
  </div>
  <div v-else class="agent-strip agent-strip-empty">
    <span class="strip-empty">Select a project above</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import AgentChip from './AgentChip.vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useRouter } from 'vue-router'

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const router = useRouter()

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

function handleChipSelect(sessionId) {
  // Set browsing project to the active project
  const session = sessionStore.getSession(sessionId)
  if (session) {
    uiStore.setBrowsingProject(session.project_id)
  }
  sessionStore.selectSession(sessionId)
  router.push(`/session/${sessionId}`)
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
</style>
