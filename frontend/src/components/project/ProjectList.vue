<template>
  <div class="project-list">
    <div v-if="orderedProjects.length === 0" class="text-muted text-center py-3">
      No projects yet. Click "New" to create one.
    </div>

    <ProjectItem
      v-for="project in orderedProjects"
      :key="project.project_id"
      :project="project"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import ProjectItem from './ProjectItem.vue'

const projectStore = useProjectStore()

const orderedProjects = computed(() => projectStore.orderedProjects)
</script>
