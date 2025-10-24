<template>
  <div class="timeline-header border-bottom p-3">
    <div>
      <div class="project-name">{{ legionName }}</div>
      <div class="view-name" :title="legionId">Timeline</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const projectStore = useProjectStore()

const legion = computed(() => projectStore.projects.get(props.legionId))
const legionName = computed(() => legion.value?.name || 'Legion')
</script>

<style scoped>
.timeline-header {
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
