<template>
  <div class="session-header border-bottom p-3">
    <div>
      <div class="project-name">{{ projectName }}</div>
      <div class="session-name" :title="sessionId">{{ sessionName }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const session = computed(() => sessionStore.sessions.get(props.sessionId))
const sessionName = computed(() => session.value?.name || props.sessionId)

// Find the project that contains this session
const project = computed(() => {
  for (const proj of projectStore.projects.values()) {
    if (proj.session_ids?.includes(props.sessionId)) {
      return proj
    }
  }
  return null
})
const projectName = computed(() => project.value?.name || 'Unknown Project')
</script>

<style scoped>
.session-header {
  background-color: #f8f9fa;
}

.project-name {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.2;
}

.session-name {
  font-size: 0.875rem;
  color: #6c757d;
  cursor: help;
}
</style>
