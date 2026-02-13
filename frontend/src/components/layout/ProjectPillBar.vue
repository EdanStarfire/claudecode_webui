<template>
  <div class="project-pill-bar">
    <ProjectPill
      v-for="project in orderedProjects"
      :key="project.project_id"
      :project="project"
      :isActive="project.project_id === activeProjectId"
      :isBrowsing="project.project_id === browsingProjectId"
    />
    <button class="pill-add-btn" @click="showCreateProjectModal" title="New project">
      +
    </button>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import ProjectPill from './ProjectPill.vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const orderedProjects = computed(() => projectStore.orderedProjects)
const browsingProjectId = computed(() => uiStore.browsingProjectId)

// Active project = the project that the currently selected session belongs to
const activeProjectId = computed(() => {
  const session = sessionStore.currentSession
  if (!session) return null
  return session.project_id
})

// Auto-set browsingProject to activeProject when session changes
watch(activeProjectId, (newId) => {
  if (newId) {
    uiStore.setBrowsingProject(newId)
  }
})

// Initialize browsingProject to first project if none set
watch(orderedProjects, (projects) => {
  if (!browsingProjectId.value && projects.length > 0) {
    uiStore.setBrowsingProject(projects[0].project_id)
  }
}, { immediate: true })

function showCreateProjectModal() {
  uiStore.showModal('create-project')
}
</script>

<style scoped>
.project-pill-bar {
  height: 44px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 16px;
  overflow-x: auto;
  flex-shrink: 0;
}

.project-pill-bar::-webkit-scrollbar {
  height: 3px;
}

.project-pill-bar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

.pill-add-btn {
  width: 28px;
  height: 28px;
  border-radius: 8px;
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

.pill-add-btn:hover {
  border-color: #3b82f6;
  color: #3b82f6;
  background: #eff6ff;
}
</style>
