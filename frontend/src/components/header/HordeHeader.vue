<template>
  <div class="horde-header border-bottom p-3">
    <div>
      <div class="project-name">{{ legionName }}</div>
      <div class="view-name" :title="overseerId">{{ overseerName }}'s Horde</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  overseerId: {
    type: String,
    required: true
  }
})

const projectStore = useProjectStore()
const sessionStore = useSessionStore()

const legion = computed(() => projectStore.projects.get(props.legionId))
const legionName = computed(() => legion.value?.name || 'Legion')

const overseer = computed(() => sessionStore.sessions.get(props.overseerId))
const overseerName = computed(() => overseer.value?.name || 'Overseer')
</script>

<style scoped>
.horde-header {
  background-color: #f8f9fa;
}

.project-name {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.2;
}

.view-name {
  font-size: 0.875rem;
  color: #6c757d;
  cursor: help;
}
</style>
