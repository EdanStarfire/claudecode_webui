<template>
  <div class="hierarchy-header border-bottom p-3">
    <div class="d-flex justify-content-between align-items-center">
      <div>
        <div class="project-name">{{ legionName }}</div>
        <div class="view-name">Minion Hierarchy</div>
      </div>
      <div class="fleet-controls d-flex gap-2">
        <button
          class="btn btn-sm btn-outline-warning"
          :disabled="isHaltAllDisabled || isOperating"
          @click="handleHaltAll"
          title="Emergency halt all active minions"
        >
          <span v-if="isOperating && lastOperation === 'halt'" class="spinner-border spinner-border-sm me-1"></span>
          Halt All
        </button>
        <button
          class="btn btn-sm btn-outline-success"
          :disabled="isResumeAllDisabled || isOperating"
          @click="handleResumeAll"
          title="Resume all minions"
        >
          <span v-if="isOperating && lastOperation === 'resume'" class="spinner-border spinner-border-sm me-1"></span>
          Resume All
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const legion = computed(() => projectStore.projects.get(props.legionId))
const legionName = computed(() => legion.value?.name || 'Legion')

// Get all minions for this legion
const legionMinions = computed(() => {
  const sessions = Array.from(sessionStore.sessions.values())
  return sessions.filter(s => s.is_minion && s.project_id === props.legionId)
})

// Count active minions
const activeMinions = computed(() => {
  return legionMinions.value.filter(m => m.state === 'active')
})

// Button disabled logic
const isHaltAllDisabled = computed(() => {
  return legionMinions.value.length === 0 || activeMinions.value.length === 0
})

const isResumeAllDisabled = computed(() => {
  return legionMinions.value.length === 0
})

function handleHaltAll() {
  uiStore.showModal('fleet-control', {
    legionId: props.legionId,
    operation: 'halt',
    activeMinionsCount: activeMinions.value.length,
    totalMinionsCount: legionMinions.value.length
  })
}

function handleResumeAll() {
  uiStore.showModal('fleet-control', {
    legionId: props.legionId,
    operation: 'resume',
    activeMinionsCount: activeMinions.value.length,
    totalMinionsCount: legionMinions.value.length
  })
}
</script>

<style scoped>
.hierarchy-header {
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
