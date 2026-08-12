<template>
  <div class="bc-dropdown bc-project-dropdown">
    <div class="dd-section-label">Switch project</div>
    <BreadcrumbProjectCard
      v-for="project in orderedProjects"
      :key="project.project_id"
      :project="project"
      @close="$emit('close')"
    />
    <div v-if="orderedProjects.length === 0" class="dd-empty">No projects yet</div>
    <div class="dd-footer-action" @click="showCreateProjectModal">+ New project</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useUIStore } from '@/stores/ui'
import BreadcrumbProjectCard from './BreadcrumbProjectCard.vue'

const emit = defineEmits(['close'])

const projectStore = useProjectStore()
const uiStore = useUIStore()

const orderedProjects = computed(() => projectStore.orderedProjects)

function showCreateProjectModal() {
  uiStore.showModal('create-project')
  emit('close')
}
</script>

<style scoped>
.bc-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: 340px;
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

.dd-empty {
  padding: 8px;
  font-size: 12px;
  color: var(--bs-secondary-color);
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
